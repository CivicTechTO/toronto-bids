# What we extract from each document type

**Decisions settled 2026-07-31.** This is a procurement archive: bids, awards, suppliers. Everything else is
classified and then deliberately ignored — and the ignoring is written down here explicitly, per
type, so that "we don't extract X" is a recorded decision rather than an omission nobody noticed.

Counts are from held documents (`background_pdf`, text extracted), 2026-07-31. The classifier
that produced them is a title-regex sketch, not a measurement — treat the numbers as scale, not
truth.

| type | TRCA | EP | Zoo | council `bgrd` | extract? |
|---|---|---|---|---|---|
| procurement award report | 211 | ~1 | 14 | **408** | **yes — full schema** |
| meeting package (>100k) | 208 | 28 | 19 | 0 | **yes — split first** |
| minutes | 169 | 33 | 38 | 0 | **yes — decision only** |
| agreement / MOU | 65 | 2 | 7 | 0 | **conditional** |
| land / property | 189 | 5 | 25 | 0 | no |
| permit / regulatory | 152 | 0 | 0 | 0 | no |
| governance / finance | 154 | 9 | 57 | 0 | no |
| update / status | 296 | 14 | 176 | 0 | **references only** |
| attachment / map / figure | 1,361 | 276 | 68 | 4 | no |
| councillor letter | 0 | 14 | 0 | 0 | no |
| agenda | 5 | 0 | 0 | 0 | no |
| untitled / unclassified | 606 | 819 | 455 | 22 | **unresolved — see below** |

---

## 1. Procurement award report — the full schema

The core type. **A document may describe SEVERAL contracts** (measured: up to four in one TRCA
report), so the document is a container and the contract is the unit.

```
document
  document_class          enum, always present
  source_url              provenance

  contracts[]             one per solicitation described
    reference             RFT/RFP/RFQ/RFSQ/VOR/contract/call number   [REQUIRED]
    solicitation_type     RFT | RFP | RFQ | RFSQ | VOR | sole_source | blanket | unknown
    title                 what is being bought
    buyer                 which body is procuring

    posted_date           when advertised
    closed_date           when submissions closed
    documents_taken       "twenty (20) firms downloaded the documents"

    declared_submissions  "Eight (8) submissions were received"   [INVARIANT — see note]
    declared_compliant    "three (3) of which were compliant"

    stage                 single_stage | prequalification_then_tender
    prequalified[]        names only — NOT bids   [STORED, see §7]
    invited_to_tender[]   names only — NOT bids   [STORED, see §7]

    bids[]
      supplier_name       verbatim, exactly as printed         [REQUIRED]
      amount_raw          verbatim, or null if none shown
      amount_basis        plus_HST | net_of_taxes | including_HST | unknown
      status              compliant | non_compliant | disqualified | withdrawn |
                          no_bid | not_stated
      rank                if the document orders them

    awards[]              several winners is normal (VOR / multi-package)
      supplier_name       verbatim                              [REQUIRED]
      amount_raw          verbatim
      amount_basis        as above
      contingency_raw     "plus 10% contingency"
      value_confidential  true when routed to a confidential attachment

    funding_source        budget line / account / partner contribution
    decision_date         when the board actually approved
    decision_body         Board of Directors / Bid Award Panel / CPO / committee
```

### Rules that are not obvious, and were each learned the hard way

- **`declared_submissions` is the runtime invariant.** It is the only field that lets the
  pipeline check itself with no human present: extract N bids, compare to the stated count,
  refuse or flag on a shortfall. Capture it even when it looks redundant.
- **Pre-qualified ≠ bid.** A firm that made a pre-qualification submission and was never issued
  tender documents *never bid*. Keeping `prequalified[]` and `invited_to_tender[]` as separate
  name lists preserves the distinction instead of silently inflating the bid list — which is a
  confirmed error class in the incumbent parser.
- **A disqualified bidder IS a bid**, with no valid price. `status` carries the reason; `amount_raw`
  is null. Matches the archive's existing `Non-Compliant` convention (#94).
- **`amount_basis` is load-bearing.** Comparing "plus HST" against "net of all applicable taxes"
  is wrong, and the raw string is the only defensible record (#74's three-tier amount rule).
- **Verbatim, always.** Names and amounts as printed, including the City's own typos
  (`Airborne Imagine Inc.`) and numeric-leading firm names (`2220742 Ontario Ltd o/a …`).
  Normalisation belongs downstream, where it can be re-run.
- **Multiple awards on one contract is normal**, not an error — Vendor of Record arrangements
  award several suppliers per service category.

---

## 2. Meeting package (>100k chars) — split, then treat as above

255 documents across sources; the largest is 8.2M characters (~2M tokens, beyond every context
window). These are containers of many items, not a document type.

**Extract:** nothing directly. **Split into constituent items first**, classify each, then apply
the schema above.

**Deduplicate.** 142 of 240 standalone TRCA award reports also appear verbatim inside a package.
Processing both double-counts. `reference` is the natural dedup key, which is another reason it
is REQUIRED.

---

## 3. Minutes — decision and date only

Minutes record the resolution, not the bid table. They carry one thing the reports often lack:
**the authoritative decision date and the body that made it.**

```
  reference            the contract the resolution concerns
  decision             approved | deferred | rejected | referred
  decision_date
  decision_body
  meeting_reference
```

**Explicitly NOT extracted from minutes:** bid lists, amounts, supplier names as primary records,
and the resolution text itself. Minutes restate; the report is the source. Use minutes to confirm
and date, never to create. *(Settled: decision date only, resolution text not retained.)*

---

## 4. Agreement / MOU — conditional

Some are procurement (a negotiated contract with a supplier — the archive already has a
`noncompetitive` keyspace for exactly this). Most are not (inter-governmental MOUs, partnership
agreements, land licences).

**Extract only when consideration flows to a supplier for goods or services:** counterparty,
subject, term start/end, value + basis, and the stated justification for not competing.
Otherwise nothing.

**This is the one type where the classifier has to make a judgement**, so it should refuse into a
review queue rather than guess.

---

## 4b. Update / status reports — award references, for cross-checking only

486 documents. They frequently restate an award: *"the contract was awarded to X for $Y"*. That
is a restatement, not a record — but it is free corroboration of a record we hold elsewhere.

```
  award_reference
    reference           the contract number mentioned                [REQUIRED]
    supplier_mentioned  verbatim, if named
    amount_mentioned    verbatim, if stated
    source_url          which report restated it
```

**These NEVER create `award` or `bid` rows.** They are a second-class record type whose only job
is to agree or disagree with the primary record.

- On **agreement**: nothing happens. Silent corroboration.
- On **disagreement** (different supplier or amount for the same reference): **surface it, do not
  resolve it.** The award report is authoritative; a conflict means one of the two is wrong and a
  human should see it. Never overwrite the primary record from a restatement.
- A reference that matches **no** primary record is the interesting case: either an award we
  never captured, or a bad extraction. Queue it, do not create it.

## 5. Types we deliberately extract NOTHING from

Listed so the decision is explicit and auditable. Each is *classified and recorded*, so the
archive knows the document exists and why it holds no rows.

| type | why nothing |
|---|---|
| **land / property** (219) | **Confirmed out of scope.** Acquisitions, disposals, easements. Public money, but no solicitation, no bidders, no supplier relationship — negotiated or expropriated. *If such a report also awards a contract (e.g. an appraisal consultant), it is a procurement award document and classifies as one.* |
| **permit / regulatory** (152) | Section 28 permits, Ontario Regulation 166/06 applications. A regulatory dataset, not procurement. |
| **governance / finance** (220) | Budgets, appointments, by-laws, policy, WSIB, insurance, audits. |
| **attachment / map / figure** (1,709) | Site plans, orthophotos, drawings, appendices. No structured content. |
| **councillor letter** (14) | Member communications. |
| **agenda** (5) | Index of items; the package and minutes already cover it. |

**Escape hatch, uniform across all of the above:** if a document classified into a "nothing" type
nevertheless contains a bid list or an award clause, it was *misclassified*. It must be re-routed
to the procurement path and the misclassification logged — never silently dropped. A "nothing"
verdict is a statement about the document type, not a licence to stop looking.

---

## 6. Untitled / unclassified — the largest open problem

**1,902 documents** (TRCA 606, EP 819, Zoo 455, council 22) do not classify. That is 31% of the
held corpus and it is not a type — it is a classifier failure. EP is the worst: 819 of 1,200.

Until resolved these must be **routed through procurement detection rather than discarded**,
because an unclassified document is not a known-empty one.

---

## 7. Storage consequences of the settled decisions

Two decisions add record types the store does not currently have. Both are deliberately kept
**out of `bid`**, because `bid` means "submitted a bid" and every count in the archive depends
on that staying true.

**Participation stages (decision 3 — stored).** A firm that pre-qualified, or was invited to
tender, competed for public work without submitting a bid. That is a real published fact and the
archive should hold it — but it is not a bid.

```
  procurement_participant
    reference | buyer      the contract
    supplier_name_raw      verbatim
    stage                  prequalified | invited_to_tender
    source_url
```

*Consequence to accept knowingly:* these names flow into the supplier dimension, so supplier
counts will include firms that never bid. That is correct — the dimension answers "who competes"
— but it changes existing totals and must be stated wherever those totals are published.

**Award references (decision 2).** As §4b. A separate table, never joined into award totals,
existing only to be compared against the primary record.

## Settled decisions (2026-07-31)

1. **Land/property: out of scope.** No solicitation, no bid, no supplier.
2. **Update/status reports: award references only**, for cross-checking, never creating rows.
3. **`prequalified[]` / `invited_to_tender[]`: stored**, in their own table, not in `bid`.
4. **Minutes: decision date only.** Resolution text not retained.

## Still open

- **1,902 unclassified documents (31%)** — §6. The largest gap, and the one that determines
  whether the counts above mean anything.
- **How packages are split into items** — §2 assumes a splitter that does not exist yet.
- Whether the classifier itself should be a model call, a rule set, or both.
