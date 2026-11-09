# City source-role cohort: independent evidence review — 2026-11-07

## Result

Reviewed the **34 proposals** and their retained original item text/publisher rows in `.validation-high-yield-recovery-2026-11-07/source-role-review.json`. Every proposed amount is the **first literal amount after Contract Award Value**, explicitly net of or excluding applicable taxes, and exactly equals its sole published supplier award. Every issued/closed witness occurs in the item text and equals the original publisher dates. No proposal selects an option-year total or computes a rounded/tax-adjusted value.

**Review finding, corrected in the final bundle:** `2016.BD108.3` originally dropped an immediately following miscellaneous allowance qualifier. The final reviewed proof pins the full literal qualification and explicitly retains this initial award as including the allowance. No amount substitution is made.

**Three method disagreements remain explicit exceptions**, not source agreement. They can proceed only through the already authorized reviewed-disagreement boundary. This review finds no reason to erase or broadly ignore them.

This is source-evidence review, not execution of a recovery or approval of an unreviewed general heuristic. No runtime, production or GitHub changes were made.

## Material findings

| Source | Finding | Required handling |
| --- | --- | --- |
| [2016.BD108.3](https://secure.toronto.ca/council/agenda-item.do?item=2016.BD108.3), RFQ `3715-16-0098` → publisher `3715160098` | Walker Environmental Group's first net award is $348,147, **including an additional 1 percent miscellaneous allowance**. The preliminary proposal stopped before that qualifier; the final proof retains it. The submitted bid is a different, lower value. | Preserve the full allowance phrase, or refuse if scope-qualified awards are outside the class. Do not describe the matched award as the unadjusted submitted bid. |
| [2014.BD173.4](https://secure.toronto.ca/council/agenda-item.do?item=2014.BD173.4) and [2017.BA31.1](https://secure.toronto.ca/council/agenda-item.do?item=2017.BA31.1), Achieva Health | Both original source recommendations and typed calls say RFP; publisher rows say RFQ. Supplier, dates and first net award match. | Preserve RFP as source method, RFQ as publisher method, and label the existing reviewed exception. |
| [2016.BD92.1](https://secure.toronto.ca/council/agenda-item.do?item=2016.BD92.1), G.W.F Construction | Source typed call is RFQ; publisher method is RFP. Source supplier punctuation differs from `GWF Construction Ltd.`. | Preserve the method conflict. Existing punctuation/legal-suffix normalization is the relevant supplier boundary; this is no new name alias. |
| [2018.BA99.6](https://secure.toronto.ca/council/agenda-item.do?item=2018.BA99.6), Brook Restoration; [2016.BD103.10](https://secure.toronto.ca/council/agenda-item.do?item=2016.BD103.10), Torcom; [2016.BD87.8](https://secure.toronto.ca/council/agenda-item.do?item=2016.BD87.8), Hawkins; [2017.BA23.8](https://secure.toronto.ca/council/agenda-item.do?item=2017.BA23.8), Struct-Con | First initial award matches publisher exactly. Source notes explain why submitted bid and award differ: alternatives, adjusted work or contingency. | Recommendation is an award-role witness, not proof of the submitted bid amount. Retain source notes; do not copy the award into the bidder-price field. |
| [2014.BD184.17](https://secure.toronto.ca/council/agenda-item.do?item=2014.BD184.17), Durham Truck & Equipment | Bid table lists multiple offers by the same supplier, including explicitly non-compliant alternatives. The recommendation names one supplier and the initial award is exact. | Link contract identity through the recommendation/award, while retaining individual bid-offer scopes. Do not collapse Bid A/B/C observations. |
| [2019.BA35.2](https://secure.toronto.ca/council/agenda-item.do?item=2019.BA35.2), Konica Minolta | Initial net recommendation is the exact publisher amount $2,820,960. Source HST-inclusive recommendation and bid/financial-impact displays differ by $1. | Net witness is exact and does not require a tolerance. Keep the inclusive-display discrepancy visible; do not infer a rounding policy. |
| [2013.BD153.1](https://secure.toronto.ca/council/agenda-item.do?item=2013.BD153.1), Joe Johnson Equipment | Recommendation uses Inc.; publisher uses Ltd. Both supply the full non-legal name. Bid table has Bid A and Bid B. | This relies on existing legal-suffix normalization, not a newly inferred company alias. Preserve bid variants. |

## Checks and review boundary

The evidence package contains 22 `reviewed_recommended_supplier` proposals and 12 ordinary `corroborated_compound` proposals. The former use the explicitly labelled recommendation to establish supplier role when title/bid-table extraction alone was insufficient; that role is more specific than loose occurrence of a supplier name somewhere in the report.

For all 34, checked witness presence, first-award position, exact monetary equality with the original nested `Awarded_Suppliers` value, single published supplier, exact issued/closed dates and preservation of publisher method. No mechanical check failed. The supplier recommendations identify a single full supplier; differences inspected are punctuation, spacing, capitalization or the existing legal-suffix boundary. No partial supplier-token match, invented alias or numeric Call Number encoding is necessary.

As an independent historical-population cross-check, searched all **7,804** untouched rows in the prior `publisher-keys/odata-full.json` snapshot using exact issued date, closing date and award amount, **without filtering by method or supplier**. Each of the 34 returned precisely the proposed document number. This supports historical uniqueness but does not replace the parent workload's required uniqueness check against its current pinned publisher snapshot.

Reviewed option-bearing Achieva, GTA Grounds Care, Walker, Firetronics and G.W.F entries separately: selected values precede the later labelled option amounts and combined totals. Scope differences elsewhere in the report must remain separate from the identity witness. An equal later option value does not change the first initial amount's role.

The source package retains original source URLs, full source text, original publisher fields, source/item/publisher hashes and the three method conflicts. Those are the reproducible evidence; derived local titles and former accepted links were not used as identity proof. Recommendation-based identity is not authority to reinterpret an observed submitted bid, and this review does not authorize fuzzy amounts or broader method-disagreement acceptance.
