# Extraction schema by document type

Decisions settled 2026-07-31. This archive tracks procurement records (bids, awards, and suppliers). Other document types are classified and skipped so omissions are explicit and auditable.

Counts below are from held `background_pdf` text on 2026-07-31 using an initial title-regex sketch (approximate scale only):

| Document type | TRCA | EP | Zoo | Council `bgrd` | Action |
|---|---:|---:|---:|---:|---|
| Procurement award report | 211 | ~1 | 14 | 408 | Extract full schema |
| Meeting package (>100k chars) | 208 | 28 | 19 | 0 | Split into items, then classify |
| Minutes | 169 | 33 | 38 | 0 | Extract decision and date only |
| Agreement / MOU | 65 | 2 | 7 | 0 | Conditional (supplier contracts only) |
| Land / property | 189 | 5 | 25 | 0 | Skip |
| Permit / regulatory | 152 | 0 | 0 | 0 | Skip |
| Governance / finance | 154 | 9 | 57 | 0 | Skip |
| Update / status | 296 | 14 | 176 | 0 | Extract award references only |
| Attachment / map / figure | 1,361 | 276 | 68 | 4 | Skip |
| Councillor letter | 0 | 14 | 0 | 0 | Skip |
| Agenda | 5 | 0 | 0 | 0 | Skip |
| Untitled / unclassified | 606 | 819 | 455 | 22 | Route to procurement detection |

## 1. Procurement award report (full schema)

A single document can cover multiple contracts (up to four observed in one TRCA report), so the document is a container and each contract is a separate item:

```text
document
  document_class          enum (required)
  source_url              provenance

  contracts[]             one per solicitation
    reference             RFT/RFP/RFQ/RFSQ/VOR/contract/call number [REQUIRED]
    solicitation_type     RFT | RFP | RFQ | RFSQ | VOR | sole_source | blanket | unknown
    title                 what is being bought
    buyer                 procuring body

    posted_date           advertisement date
    closed_date           submission closing date
    documents_taken       e.g. "twenty (20) firms downloaded the documents"

    declared_submissions  e.g. "Eight (8) submissions were received" [INVARIANT]
    declared_compliant    e.g. "three (3) of which were compliant"

    stage                 single_stage | prequalification_then_tender
    prequalified[]        names only, not bids (stored separately)
    invited_to_tender[]   names only, not bids (stored separately)

    bids[]
      supplier_name       verbatim as printed [REQUIRED]
      amount_raw          verbatim, or null if absent
      amount_basis        plus_HST | net_of_taxes | including_HST | unknown
      status              compliant | non_compliant | disqualified | withdrawn |
                          no_bid | not_stated
      rank                if ordered by the document

    awards[]              multiple winners are normal (VOR / multi-package)
      supplier_name       verbatim [REQUIRED]
      amount_raw          verbatim
      amount_basis        as above
      contingency_raw     e.g. "plus 10% contingency"
      value_confidential  true if routed to a confidential attachment

    funding_source        budget line / account / partner contribution
    decision_date         board or panel approval date
    decision_body         Board of Directors / Bid Award Panel / CPO / committee
```

### Schema rules

- **`declared_submissions` is the completeness check.** The pipeline compares extracted bid counts against this number and flags shortfalls.
- **Pre-qualified firms are not bidders.** Firms that pre-qualified or were invited to tender without submitting a bid go into `prequalified[]` / `invited_to_tender[]`, never into `bids[]`.
- **Disqualified bidders are bidders.** A firm that submitted a bid and was disqualified belongs in `bids[]` with `amount_raw = null` and `status` set, matching the archive's `Non-Compliant` convention (#94).
- **Keep `amount_basis` explicit.** Never compare "plus HST" against "net of all applicable taxes"; store raw strings verbatim (see #74).
- **Keep names and amounts verbatim.** Preserve typos (`Airborne Imagine Inc.`) and numbered-company prefixes (`2220742 Ontario Ltd o/a ...`). Normalization happens downstream.
- **Multiple awards per contract are expected.** Vendor of Record and multi-package contracts routinely have multiple winners.

## 2. Meeting packages (>100k chars)

Observed: 255 documents, up to 8.2M characters (~2M tokens). These bundle many unrelated items.

- Split into individual items first, classify each item, and apply the schemas above.
- Deduplicate on `reference`: 142 of 240 standalone TRCA award reports also appear verbatim inside meeting packages.

## 3. Minutes (decision and date only)

Minutes record the formal approval date and decision body that staff reports often lack:

```text
  reference            contract reference
  decision             approved | deferred | rejected | referred
  decision_date
  decision_body
  meeting_reference
```

Do not extract bid lists, amounts, supplier names, or full resolution text from minutes; use minutes only to confirm and date decisions from primary reports.

## 4. Agreements and MOUs (conditional)

Extract only when a contract pays a supplier for goods or services (fitting the `noncompetitive` keyspace): counterparty, subject, term start/end, value and tax basis, and sole-source justification. Skip intergovernmental MOUs, partnerships, and land licences. Route borderline cases to a review queue.

## 5. Update and status reports (award references only)

Observed: 486 documents. These often mention past awards in passing and serve only as a cross-check:

```text
  award_reference
    reference           contract number [REQUIRED]
    supplier_mentioned  verbatim, if named
    amount_mentioned    verbatim, if stated
    source_url          report URL
```

These never create `award` or `bid` rows:

- **Matches primary record:** silent corroboration.
- **Conflicts with primary record:** flag for human review; never overwrite the primary report.
- **No primary record found:** queue for review (possible missing award or extraction error).

## 6. Skipped document types

Each document is still classified and recorded so the reason it has no extracted rows is auditable:

| Type | Count | Reason skipped |
|---|---:|---|
| Land / property | 219 | Acquisitions, disposals, and easements have no solicitation or supplier relationship. (Exception: if a property report also awards a consulting contract, classify it as a procurement award.) |
| Permit / regulatory | 152 | Section 28 permits and Ontario Regulation 166/06 applications. |
| Governance / finance | 220 | Budgets, appointments, by-laws, policy, WSIB, insurance, audits. |
| Attachment / map / figure | 1,709 | Site plans, orthophotos, drawings, visual appendices. |
| Councillor letter | 14 | Member communications. |
| Agenda | 5 | Index only; covered by packages and minutes. |

If a document classified into a skipped type actually contains a bid list or award clause, re-route it to procurement extraction and log the misclassification.

## 7. Unclassified documents

1,902 documents (31% of held text: TRCA 606, EP 819, Zoo 455, council 22) did not match the initial title rules. Route unclassified documents through procurement detection rather than skipping them.

## 8. Storage notes

Two record types are kept out of `bid` and `award` so existing counts stay accurate:

- **Participation stages (`procurement_participant`):** Stores `prequalified` and `invited_to_tender` firms with `(reference | buyer, supplier_name_raw, stage, source_url)`. Because these names enter the `supplier` dimension, supplier counts will include firms that competed without submitting a bid.
- **Award references:** Stored in a separate table for cross-checking only, never summed into award totals.

## Open questions (as of 2026-07-31)

- Resolving the 1,902 unclassified documents (later addressed by the classifier in `docs/ground-truth/document-classification/`).
- Splitting large meeting packages into individual items.
