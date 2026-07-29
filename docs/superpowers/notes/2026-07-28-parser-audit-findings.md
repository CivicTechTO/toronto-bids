# Parser audit findings (#151, #203) — 2026-07-28

Measured against each corpus's **own** ground truth, not against the parser being replaced.

## EP board reports (#151) — SWITCHED to cells

Four rules: caption anchor, page-break walk, row = name + price-or-outcome, normalise.

| | |
|---|---|
| reports carrying a `Table 1` caption | 47 |
| of those, reports where the anchor found the table | **47/47** |
| rows extracted | **153** (regex: 143) |
| rows failing a name sanity check | **0** |
| duplicate rows within a report | **0** |
| agreement with the reports' own declared bid counts | **32/35 exact** |
| real bidders lost against the regex | **0** |
| stored `agency_bid` rows | 115 → **129** |

The rule count started at four and finished at four. Both wrinkles found mid-measurement
collapsed into rules already written rather than adding new ones: the two page-break shapes
(caption stranded at a page foot; rows continuing overleaf as a separate headerless table) are
one event and one walk, and the `Non-compliant`-in-the-price-column case turned out to be #94's
existing rule for the BD agendas, not a new one.

**This is not a net removal.** 19 rows go (13 prose phantoms, 6 `Table 2` duplicates) and 14+
real bidders the regex silently dropped come back: firms whose name begins with a digit
(`1214592 Ontario Limited o/a Colonial Building Restoration`), firms whose price carries a
leading marker (`*$792,900.00` — which cost `backgroundfile-244900` its *winning* bidder), and
prices published without cents (`$4,365,534`, which the old `\.\d{2}` requirement read as "no
bids at all").

The 3 residual count disagreements are the City's documents, not the parser, and no rule was
added for them: `238908` and `244923` say outright that they tabulate only the compliant subset
(*"four (4) submissions were received, three (3) of which were compliant"*), and `254543`
carries a malformed price in the City's own PDF (`$1,479,386,.57`).

## TRCA eSCRIBE (#203) — NOT switched; regex is correct and stays

The switch criterion fails on its strongest clause: **cells lose real rows.**

| | |
|---|---|
| documents where the incumbent finds bidders | 144 |
| tables pdfplumber finds in them | 3,233 |
| of those, bid-shaped tables | **95** |
| bid rows cells would yield | **356** |
| bid rows the incumbent yields | **527** |

The individual tables are excellent — `["Proponent", "Fee (Plus HST)"]`, cleanly ruled, exactly
the shape that made EP worth switching. The corpus is the problem, and in a way EP never was:

- **A TRCA "report" is a whole meeting package**, not one report. 85 tables across 102 pages,
  6–8 separate bid tables per document, each belonging to a different item and a different
  solicitation reference.
- **Only 25 of the 55 documents holding a bid table have an unambiguous one-table-one-ref
  mapping. 30 do not**, and attributing a table to the right solicitation inside a 102-page
  package is a rule class EP never needed — locating the nearest preceding reference by page
  position, with nothing published to confirm it against.
- **89 of the 144 documents have no ruled bid table at all**, yet the incumbent extracts real
  bidders from them via the prose bullet list. Those bidders exist only in prose.

So switching would trade 527 attributable rows for 356 rows of which 30 documents' worth could
not be attributed at all. The rule count also climbs rather than holding — a new attribution
rule plus a fallback for the 89 prose-only documents — which is the divergence signal, not the
convergence one. **Regex stays. Recorded so it is not re-litigated.**

**One bounded follow-up worth its own issue, not done here.** TRCA's 408 stored bids currently
carry **zero prices**. On the 25 documents with an unambiguous single bid table, cells could add
prices with no attribution ambiguity at all. That is a real gain, but it is a different change
with a different justification, and folding it into this audit would be exactly the scope creep
the audit exists to avoid.

## Zoo ZB legdocs (#203) — NOT switched; the structure is absent from the corpus

| | |
|---|---|
| documents scanned | 859 |
| tables pdfplumber finds | 2,713 |
| of those, bid-shaped tables | **6** |
| bid rows cells would yield | **17** |
| bid rows stored today | 0 |

**6 documents in 859 (0.7%)** carry a ruled bid table. This is the #83 profile exactly: the
structure is missing from the documents, not from the parser, and no extractor invents it.

The six that do exist are excellent — `PROPONENT / PROPOSAL PRICE / SCORE (OUT OF 150)`, with
the evaluation score in its own column, which is richer than anything EP publishes. That is
precisely why the number matters: the tables are not the problem, their absence is. Building a
Zoo cell path for 17 rows across 859 documents is not worth a parser, and would land the same
attribution question TRCA failed on. **Recorded, not built.**

## Committee award reports (#203) — NOT switched, and a naive rule here would be harmful

8 held reports; 7 carry a ruled table and 5 carry a money-bearing one. But **none of the
money-bearing tables is a bid table.** They are budget cash-flow tables:

```
['Year', 'CH2M Hill\nCanada Limited', 'Stantec\nConsulting Limited', 'Total']
['2023', '$3,821,411', '$4,142,471', '$7,963,882']

['Term', 'Contract Total']
['Initial Term - July 1, 2023 to December 31, 2023', '$5,309,334.15']
```

A "first ruled table containing money" heuristic — the one #151 explicitly declined to use, and
validated against instead — would here produce **years and contract terms as bidders** and
account numbers as suppliers. The single `Supplier`-headed table is a multi-winner *allocation*
table (winners × per-year amounts), not a list of losing bidders.

This corroborates #164 on its own terms: **RFTs and RFQs tabulate bids; RFPs narrate them**, and
the committee tier is dominated by the latter. The existing `_BID_TABLE_ANCHORS` refusal is
correct behaviour and stays.

## Audit summary

| corpus | held | ruled bid tables | verdict |
|---|---|---|---|
| EP board reports | 1,200 | **47/47** of those with a caption | **switched to cells** |
| TRCA eSCRIBE | 3,411 | 95 tables, but 30 documents unattributable | regex stays |
| Zoo ZB | 859 | **6** | regex stays |
| committee awards | 8 | 0 real bid tables | regex stays |
| council staff reports | 434 | 13–20/229 (#83, prior) | regex stays |
| Award Summary Forms | 238 | 229/229 (#116, prior) | already cells |

One of five candidates switched. That ratio is the finding, not a disappointment: "read cells
where the PDF HAS cells" is a per-corpus fact, and four of these corpora do not have them.

## Separate defect found while verifying EP — not fixed here

Five EP reports parse a **correct** bid table whose rows are then discarded, because
`parse_ep_report` — the *prose award-clause* parser, not the bid-table parser — refuses the
report as a non-award: `157467` (4 bids), `167548` (5), `229322` (5), `244900` (4), `254716` (6).
**24 real bids, correctly extracted, thrown away.**

This is a defect in #130's award-clause regex, not in #151's table parser, and fixing it here
would mean rewriting a prose parser under a ticket about tables. Worth its own issue.
