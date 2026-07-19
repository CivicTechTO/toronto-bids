# Pre-Ariba bid bridge — the first slice of the surrogate spine (#124)

**Date:** 2026-07-19
**Status:** approved (autonomous — maintainer reviews at the PR), not yet implemented
**Delivers:** the actionable core of #124 identified by the zero-bid investigation (#163): link the
already-captured pre-Ariba council-agenda bids to their spine solicitations, so "was this
competitive?" becomes answerable for ~1,288 pre-2019 awards — with **no new scraping**.

## 1. Scope decision

#124 as written is a full keyspace re-architecture (a surrogate `id` re-homing every table and the
export). That is a multi-PR epic and not what #163 needs now. #124's own framing is that the
surrogate key "only gives you somewhere to store a match you can justify" — the *matching* is the
work. This ships exactly that: **a place to record the pre-Ariba `reference ↔ document_number`
equivalence, populated by #77's proven matcher, and consumed by the export** — without the
schema-wide re-architecture, which remains #124's larger scope.

## 2. The mechanism (and why not the obvious one)

**Rejected:** backfilling `bid.document_number` on matched pre-Ariba bids. `bid_key` is
`COALESCE(reference,''), COALESCE(document_number,''), bidder_name_raw, …`; adding a
`document_number` changes the key, and `store_bids` re-creates the reference-only row every sync →
**duplication**. Not idempotent.

**Chosen:** a mapping table, rebuilt each run (like the supplier dimension), that the export
consults. No bid mutation, fully idempotent, and faithful to #124 ("somewhere to store the
equivalence").

```sql
CREATE TABLE solicitation_link (
    reference        TEXT PRIMARY KEY,   -- a pre-Ariba council item, e.g. '2016.BD106.3'
    document_number  TEXT NOT NULL,      -- the spine solicitation it is the same procurement as
    method           TEXT NOT NULL       -- 'council_pre_ariba' (the matcher that established it)
);
```

## 3. Populating it — reuse #77's matcher

The equivalence is established the same way #77 already names pre-Ariba titles: match a council
item's **(winner, award value net-of-taxes)** to a solicitation's award, **unique match only**.
#77's calibration against 777 Ariba-era ground-truth items measured **0 wrong at 97.7% recall** —
that calibration *is* the reference→document_number precision, because #77 matches items to
document numbers and checks the number. A wrong merge is worse than none; only a unique match is
taken.

Two small changes to reuse it for the reference (not just the title):
- `parse_pre_ariba_awards` gains a `"reference"` on each item (the council item ref it already sits
  under — extracted the same way `parse_agenda` does), so a match can be recorded against the
  reference, not only used to fill a title.
- A new pass `match_pre_ariba_solicitations(conn, agendas) -> int` parses the pre-Ariba items,
  matches each **(winner, value)** to a solicitation, and upserts unique matches into
  `solicitation_link`. It matches against **all** awards (not only title-less ones), because a
  solicitation that already has a title still needs its bids linked — so this is a *new* match
  surface and gets its own live calibration (§6), not an assumed inheritance of #77's number.

It runs in the offline `enrich-titles` agenda flow, beside `match_pre_ariba_titles` (same cached
agendas, no browser). Idempotent: the table is cleared and rebuilt from the current match each run.

## 4. Export — attach the bids (and staff reports) to the solicitation

`export/document.py` already builds a `reference → document_number` bridge (from dual-key bids,
#126) and uses it for staff-report attachment. Two contained changes:

1. **Union `solicitation_link` into that bridge.** Pre-Ariba staff reports (#126) then attach to
   their solicitation automatically — same code path, more coverage.
2. **Nest a bridged pre-Ariba bid under its solicitation.** In the bid-nesting: a bid with a
   `reference` that the bridge maps to a solicitation `document_number` nests under that
   **solicitation** (`solicitations[].bids`); an unbridged reference bid stays under its
   `council_item` (unchanged). Each bid lands in **exactly one** bucket, so #145's reconciliation
   invariant (`council_items[].bids + solicitations[].bids + unlinked_bids == meta.counts.bid`)
   still holds — the bridged bids simply move from the council-item bucket to the solicitation
   bucket.

A council item and its matched solicitation are the *same procurement* (that is what the match
asserts), so surfacing the bids under the solicitation — the canonical procurement record the
frontend uses — is correct, not a relocation of unrelated data.

## 5. Data flow

`enrich-titles` (cached agendas) → `parse_pre_ariba_awards` (now carrying `reference`) →
`match_pre_ariba_solicitations` (unique (winner,value) match) → `solicitation_link` upsert →
export's bridge unions `solicitation_link` → pre-Ariba bids + staff reports nest under their
solicitation. No sync-path or browser change; `tb enrich-titles` (offline) already runs the
agenda passes.

## 6. Testing

- **Pure matcher tests** (offline, fixtures): `parse_pre_ariba_awards` carries the right reference;
  a unique (winner, value) match records a `solicitation_link` row; a non-unique or no-supplier
  match is dropped (a wrong merge is worse than none); idempotent rebuild.
- **Export tests**: a bridged pre-Ariba bid nests under its solicitation and NOT its council item;
  an unbridged reference bid stays under its council item; #145's reconciliation invariant still
  holds; a pre-Ariba staff report attaches to the solicitation via the unioned bridge.
- **Mandatory live-calibration gate (the crux, per #77/#96/#136).** Because matching against *all*
  awards is a new surface, calibrate precision against ground truth before declaring done: take
  Ariba-era references whose `document_number` is known (dual-key bids), run the (winner, value)
  match **ignoring** that number, and measure how often it recovers the *correct* document_number
  (false-merge rate) and the recall. If the false-merge rate is not ~0, tighten the guard (require
  the supplier token; drop non-unique) before shipping. Record the measured false-merge rate and
  the recovery count (references linked, bids attached) — a wrong merge is worse than none.

## 7. Out of scope

- The full surrogate-`id` spine table and re-homing every keyspace (the rest of #124).
- Composite-era (2009-2012) items — they publish no bidder list (#93), so there are no bids to
  bridge even where the award matches.
- The Council/Standing-Committee mega-award bids (#164) — a different, un-captured source.
- Any change to how bids are parsed or stored; this only records equivalence and re-homes the
  export nesting.

## 8. Recording

On completion, comment on #124 (this is its first, highest-value slice) and #163 (the zero-bid
recovery it delivers) with the measured false-merge rate and the recovery count (references linked,
bids attached, and the drop in the zero-bid solicitation count).
