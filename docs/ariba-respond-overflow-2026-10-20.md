# Ariba Respond action in the overflow menu — October 20, 2026 (#241)

## Diagnosis

The authenticated Discovery preview puts Respond inside its **Additional Options** overflow menu on all seven affected events. The existing capture code verified an event ID marker and immediately queried the Respond button without opening that menu. The marker and open status were present, but the Respond button was not exposed to the locator.

A fresh authenticated Chromium session reproduced the original timeout on posting 1110026792 with a shortened five-second locator timeout. Inspecting the visible UI found the correct event, status Open, and no Respond button. Opening Additional Options revealed Respond, Not Interested, and Save Lead. A comparison against posting 1110027542 showed Respond directly, and the same read-only action inspection confirmed overflow behavior on the other six affected postings.

This is an event-action layout problem. No endpoint, credentials, or permission checks were changed. The subsequent live captures exposed additional download and access outcomes described below; opening the menu alone does not establish that every document can be recovered.

## Change and tests

Capture now waits for a visible Respond action. When it is not directly visible, it opens the specifically named Additional Options menu once and requires Respond to appear. It waits for delayed controls rather than treating the event ID marker as action readiness. An absent action remains an explicit failure with the posting ID. Disabled Respond handling and explicit permission-denial handling remain in place.

Regression tests run through the actual capture entry point for inline/overflow layouts, controls that appear after the identity marker, missing actions, an unrelated menu, disabled Respond, and permission denial. Before the fix the inline path passed and the six affected cases failed. Afterward, all seven passed; the focused archive/capture checks passed (122 tests), and the complete offline suite passed (**865 tests**).

## Live verification

The corrected capture ran against all seven events, saving bytes in an isolated recovery directory. All seven got past the original missing-Respond failure. Every emitted zip passed CRC validation.

| Document | Posting | Outcome | Files retained / gap |
|---|---|---|---|
| 5793328657 | 1110026792 | Partial bundle | 2 leaf files; omitted: Part 2 - Form of Agreement.pdf; Part 3 - Appendices.zip |
| 5881114083 | 1110028659 | Partial bundle | 3 leaf files; omitted: Part 2 - Form of Agreement - version 1.3 1006.pdf |
| 5886345486 | 1110028656 | Explicit permission denial | Sourcing page reported no permission to view the event; no bypass attempted |
| 5890380569 | 1110028506 | Explicit permission denial | Sourcing page reported no permission to view the event; no bypass attempted |
| 5846718291 | 1110028671 | Partial bundle | 2 leaf files; omitted: Part 2 - Form of Agreement.pdf |
| 5873812160 | 1110027891 | Below capture floor; remains pending | 2 of 5 listed documents downloaded; partials retained, no canonical zip |
| 5830474526 | 1110028474 | Partial bundle | 2 leaf files; omitted: Part 2 - Form of Agreement.pdf |

Four partial bundles hold nine indexed leaf files. Two more individual downloads from the trailers event remain in its working partials. No complete-bundle claim is made. The unresolved popup/reference-menu behavior is tracked by #183; automatic incomplete-bundle retries are #243, blocked by the overwrite guard in #199.

Two events—lubricants (5886345486) and engineering services (5890380569)—returned the explicit Sourcing access-denial message after Respond. That establishes the archival account’s current restriction, not the buyer/SAP policy behind it. Escalation to the buyer or SAP is needed if access is expected; no access restriction was bypassed.

## Preservation and production state

All recovery bundles, omission records, retained partials, per-event results, and the capture log were copied and hash-verified into the new durable staging directory:

`/home/alex/tb-data/ariba/_241_recovery_20261020/`

No existing production bundle was overwritten, and none of these seven events received a canonical zip in the normal production archive during verification. The production database/index and published artifacts were not changed by this verification. Keeping partial captures staged avoids having the current zip-exists rule mark them archived forever before #243 is implemented.

## Submission deadlines

The live comparison recorded each posting’s advertised close date and the OData spine deadline where present. The earliest affected posting advertises October 26 at 16:00 UTC, and has no spine deadline. Its newly recovered “Part 1 - RFQ Process REVISED” says the City Online Procurement System countdown takes precedence over the Discovery posting or other advertised dates. Treat the source’s live sourcing countdown as authoritative when scheduling recovery. The other six OData dates match their advertised close days (November 6, 13, 16, or 19).

A final read-only visit to the earliest event’s sourcing page showed **6 days 01:51:26 remaining** at approximately **2026-10-20 14:08:34 UTC**, consistent with the October 26 16:00 UTC deadline (the observation has sub-second capture/rounding uncertainty). The visible sourcing-page text was retained with the staged evidence.
