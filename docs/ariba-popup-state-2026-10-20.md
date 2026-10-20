# Ariba popup state investigation (#183)

Follow-on to #241 / PR #245. All live downloads used isolated staging under
`/tmp/tb-183`; existing production archives and the attachment index were left intact.

## Reproduction and cause

On open event `1110026792` (`Doc5793328657`), downloading Part 1 immediately followed
by Part 2 reproduced the missing visible “Download this attachment” menu. The correct
Part 2 anchor received the pointer and click events. Its row was present and visible.

Ariba still reported `ariba.Request.isRequestInProgress() == true` after Playwright's
download `save_as` completed. Waiting for that function to return false, with every
other action unchanged, recovered all four files. Observed waits before subsequent
actions were 5.627, 3.930 and 5.642 seconds. The function is a state getter; the scraper
does not change the state. A guessed delay or repeated click would obscure the cause.

The References controls are PML triggers for mutually exclusive `.awmenu` popups.
Their `_mid` attribute names the associated menu id, including after the widget moves
that menu into a `MovedCopy` wrapper. The attachment's containing menu therefore
identifies the exact trigger needed to restore it. This is the outer `.awmenu` id,
not the id of an inner attachment-list div. These runtime ids are used only
for DOM actions, never for archive fingerprints or resume identity.

Hidden menu items remain in the DOM. Opening an already-rendered popup can reveal
files without changing the number of anchors, explaining the old “revealed nothing”
message. Expanding every References control leaves the last popup visible and can hide
the document being sought in another popup. Escape is unreliable for the widget's
anchor triggers; its key handler treats button triggers differently.

A larger open event, `1110019796` (`Doc5793587590`), exposed a second concrete failure:
the expanded References popup intercepted clicks on all three top-level Part controls.
Playwright's action log identified PMI items inside the References `MovedCopy` subtree
as the obstruction. Its 12 reference attachments were individually reachable. Waiting
for request idle alone therefore fixes the sequential-action failure but does not
resolve all popup state failures.

The large popup also covers its own trigger, so a mouse click on the correct owner
still fails. Enter initially appeared to toggle the popup, but full capture disproved
that assumption. The widget's `menuLinkOnKeyDown` delegates to `menuKeyDown` when its
menu is already active; Enter can select an item. Directly dispatching a click event
also left the popup unchanged. Neither is an adequate dismissal mechanism.

The widget's own `ariba.Menu.hideActiveMenu()` was then verified to change the visible
References menu to hidden, clear the active-menu association, and leave the request
gate idle. A normal click on the identified References owner restored the same menu.
This is the operation the widget's event handlers use. The scraper calls it and
verifies its postcondition, without assigning internal state fields.

## Change

Before each document action, wait up to 30 seconds for Ariba's request gate to become
idle. An unavailable state getter or a request that never completes is a failure.
Keep the existing check for a previous document's visible Download menu.

Before a top-level PML click, dismiss visible popups through the widget's
`Menu.hideActiveMenu()` operation and verify the original menu ids become hidden.
For a hidden reference attachment, identify its unique `_mid` owner, dismiss any
obstructing popup, open only the required menu, and verify the attachment becomes
visible. Missing or ambiguous ownership fails rather than guessing. Document order,
identity, fingerprints, resume rules and archive replacement guards remain unchanged.

## Verification

- Regression tests first reproduced the consecutive-download failure and the failure
  to restore a document from the first of two mutually exclusive menus.
- Tests cover both document control kinds, a permanently busy request, switching
  between menus, closing an obstructing popup, and missing or ambiguous owners.
- Full offline suite: **872 passed**.
- Fresh captures through `capture_event` with the complete change:

  | Event | Captured / listed files | Recursive leaves | Seconds |
  | --- | --- | --- | --- |
  | `Doc5793328657` | 4 / 4 | 17 | 39.7 |
  | `Doc5793587590` | 15 / 15 | 33 | 147.7 |

  Both archives pass `ZipFile.testzip()`, have no download failures and no omission
  sidecar. Copies were SHA-256 verified into
  `/home/alex/tb-data/ariba/_183_recovery_20261020/`; `verification.json` records the
  hashes, counts, timings, logs and existing canonical archive hash where applicable.
- An additional live listing of `Doc5749779772` exposed 17 direct document controls
  and no References menus. Switching between two References menus is covered by the
  stateful regression test; the two successful full captures each had one such menu.

The original 39-file event from July has closed, so the old failure cannot be replayed
against that original live widget. The preserved July backup remains untouched.
The current larger event lists 15 files while its existing archive contains 22,
including seven Part 4/5 forms absent from today's traversal. The existing archive
also preserves original versions of the now-revised Parts 1 and 2. A complete capture
of today's listing therefore does not establish historical completeness and must not
replace the stored archive. This work leaves it intact.
