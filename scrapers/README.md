# Toronto Bids scraper

A `uv`-managed Python package that pulls City of Toronto procurement data into a
local SQLite store. No browser, no login, no cloud.

## Sources (P0/P1)

- **OData `feis_solicitation_published`** — the solicitation lifecycle spine
  (open / awarded / cancelled), the authoritative source.
- **OData `feis_non_competitive_published`** — non-competitive (sole-source) awards.
- **CKAN `tobids-awarded-contracts` / `tobids-all-open-solicitations` /
  `tobids-non-competitive-contracts`** — backfill for the spine.
- **SAP Ariba Discovery** (`ariba_discovery`) — archives currently-open City-of-Toronto
  Ariba postings (`ariba_posting` table) before they close, via public JSON APIs (no auth).
  Each posting is bridged to its `document_number` where the detail endpoint resolves
  (~40% return HTTP 500 on a given run and are archived un-bridged; idempotent re-runs fill
  the gap). The `sourcing_url` column is the authenticated event link for a future
  attachments phase.
- **Suspended & Disqualified Firms** (`suspended_firms`) — the City's public registry of
  suspended/disqualified suppliers (`suspended_firm` table), parsed from the HTML table. Each
  row carries the supplier name, status, suspension dates, type, and the council `Authority`
  reference. Exported as a top-level `suspended_firms` array.
- **Council enrichment** (`tb enrich-council`, OPT-IN) — for each suspended firm, fetches its
  City Council decision from TMMIS and the linked staff-report / communication PDFs
  (`council_item` + `background_pdf` tables, with extracted text). TMMIS is Akamai-gated and
  only served to a **real, headed browser**, so this command drives a headed Chromium
  (Playwright); the PDFs themselves download over plain HTTP + `pdftotext`. It is **not** part
  of `tb sync` — the core pipeline stays browser-free, so Playwright lives behind the optional
  `council` extra (the default install pulls neither Playwright nor `pyvirtualdisplay`). Enable
  it with `uv sync --extra council && uv run playwright install chromium`. On a headless server
  run `tb enrich-council --virtual-display` with `Xvfb` installed (`apt-get install -y xvfb`);
  `pdftotext` (poppler) is also required.
- **Supplier dimension** (`supplier` table) — after every sync, a linking pass canonicalizes
  the free-text supplier names across awards, non-competitive contracts, and suspended firms
  into one `supplier` row per firm (merging spelling/case/punctuation variants; legal suffixes
  kept so distinct entities stay distinct) and backfills a `supplier_id` FK on those rows. The
  export includes a top-level `suppliers` array; each award/non-competitive/suspended-firm
  record keeps its `supplier_id` so you can answer "which contracts belong to this supplier?".

Everything competitive is keyed on the normalized 10-digit `document_number`.
Non-competitive awards are a separate keyspace (`workspace_number`), and 2009-2012
awards are a third (`composite_award.call_number`) — they predate Ariba, so they carry
a Call Number and join to neither.

## Usage

```shell
cd scrapers
uv sync
uv run tb sync            # fetch all sources into files/bids.sqlite
uv run tb sync --only odata_solicitations,ckan_awarded
uv run tb status          # row counts + last run per source
uv run tb export [--out PATH]  # write the whole store to a single JSON artifact
uv run pytest             # tests (offline; uses fixtures)
```

Each source runs in isolation: one source failing never stops the others, and whatever
the others fetched is still committed. Failures are not silent, though — `tb sync` prints
each one to stderr and **exits non-zero** if any source failed, so cron and CI notice.
`tb status` shows the last run per source with its status and error, which is where you
look first when a number seems wrong.

### Feed drift

`sources/schema_check.py` declares the fields each normalizer reads out of the City's
OData and CKAN feeds, and a `schema_check` source samples one record per feed on every
sync to confirm they're still there. If the City renames or drops a field, the run fails
loudly instead of quietly NULLing that column across every row.

It's an ordinary source, so per-source isolation applies deliberately: **drift is reported
without stopping ingestion**. A renamed buyer-phone field should never cost us the archive
of a posting that disappears when it closes. When it fires, fix the normalizer and the
declared field set together — they're two halves of one change.

- `uv run tb export [--out PATH]` — write the whole store to a single
  solicitation-centric nested JSON artifact (default `<DATA_DIR>/export/bids.json`):
  each solicitation with its `awards` and `ariba_postings` nested by `document_number`,
  plus top-level `noncompetitive`, `unlinked_ariba_postings` (Ariba postings whose
  document_number never bridged to a solicitation), and `unlinked_awards` (awards
  whose document_number matches no solicitation). This is the publish seam:
  `build_export_document(conn)` builds the format-independent document and
  `export_json()` serializes it, so another destination/format is another function
  over the same builder — no change to the document shape.

Set `TB_DATA_DIR` to change where `bids.sqlite` and downloads live (default `scrapers/files/`).

See `../docs/superpowers/specs/2026-07-14-toronto-bids-scraper-rewrite-design.md`
(from repo root) for the full design, source inventory, and the later phases
(Ariba Discovery JSON, attachments, council/PDF enrichment).

## Data notes

Four things will give you a wrong answer if you assume the obvious.

**Never `SUM(award_amount)`.** It is `TEXT`, holding the City's string verbatim —
`"$1,317,169.92 CAD"`, `"kj"`, and in a few cases three amounts concatenated. SQLite
coerces text prefixes, so that sum silently returns **$950 trillion**; and because text
sorts above every number, `award_amount > 1000` matches every row that *has* an amount,
whatever the amount is. Aggregate **`award_amount_numeric`** (`REAL`) instead — same for
`contract_amount_numeric`. Where the numeric is NULL beside a non-NULL raw string, the raw
value is not a single CAD amount (67 of 14,165 awards). That is deliberate, not missing
data: the string is kept because it is what the City published, and some of it has no
numeric form at all.

**Amounts come in three tiers.** `award_amount` is raw, `award_amount_numeric` is what the
machine could parse from it, and `award_amount_labelled` (+ `award_amount_verdict`) is human
judgement on the 35 strings the parser refuses — `'S2,035,000.00'` really is $2,035,000.00,
`'31.65/MT'` really is a rate and not a total. The verdicts are a reviewable file
(`toronto_bids/data/amount_labels.toml`), not a database someone clicked, so `git blame` says
who decided and which PR argued it.

```sql
SELECT SUM(award_amount_numeric) FROM award WHERE source = 'odata';                     -- machine only
SELECT SUM(COALESCE(award_amount_labelled, award_amount_numeric)) FROM award
 WHERE source = 'odata' AND COALESCE(award_amount_verdict, '') != 'not_an_award';       -- opts into human calls
```

Both are honest; neither is silently mixed. `tb amounts unlabelled` lists anything the parser
refused that nobody has ruled on yet.

**One `award` row is one award *line*, not one supplier.** A document can award the same
supplier many times — standing-offer call-ups are routine, and `Cascades Recovery Inc.` has
ten lines on document `9154157025`. So **do not `GROUP BY document_number,
supplier_name_raw`** to de-duplicate: that collapses real, distinct awards.

**Do de-duplicate by source.** OData (`source='odata'`) is the spine and CKAN's
awarded-contracts dataset (`source='ckan_awarded'`) is an independent cross-check, so every
award legitimately appears once per source. Filter to `source='odata'` for a single view.
Both hazards apply at once — a trustworthy total needs the numeric column **and** a source
filter:

```sql
SELECT SUM(award_amount_numeric) FROM award WHERE source = 'odata';
```

Even then, treat the figure with care: three awards are implausible in *both* City feeds
(document `3901175008` publishes `9054510208` — $9.05B to an individual, against a ~$16B
city budget) and carry roughly $15B of the total. The sum is faithful to what the City
published; what the City published is wrong.

**The archive reaches back further than the City's feed does.** For 2009-2011 the feed
publishes 13 awards in total. The `composite_award` table holds **1,052 awards worth $2.37B**
for 2009-2012, recovered from the Bid Committee's composite staff reports (#96). They
predate Ariba, so they are keyed on **Call Number** and join to `solicitation` not at all —
query them on their own, and aggregate `award_value_numeric`, which is the initial contract
term and deliberately excludes the option years published beside it.

**Most solicitations have no title.** For ~72% of them the City publishes the document
number *as* the title (`Doc-3524228095`), which carries no information the primary key does
not. Those are stored as `NULL`, so **`title IS NULL` means "the City published no title"**
— do not test `title LIKE 'Doc-%'`, which both misses placeholders (`'3586141004'`) and
catches real titles that merely lead with a document number.
