# Ariba training-event capture and source restrictions (#242)

Investigation date: 2026-10-20. The two reported Sourcing-load timeouts were explicit
Discovery rejections that the wait loop did not recognize. Other training events were
accessible, including events previously excluded or observed to deny access. The corrected
flow captured three supported events and reported the two current restrictions separately.

## Live diagnosis

The baseline used the existing login and `capture_event` functions in an ordinary headed
Chromium session, saving captures separately. Both original failures reproduced after
Respond; the browser stayed in Discovery and displayed a rejection heading. Neither reached
the Sourcing Download Content action.

| Document | Discovery posting | Initial evidence | Final identity-verified run |
| --- | --- | --- | --- |
| 3861692164 | 15346406 | Generic timeout; Discovery displayed the public-sector sourcing-disabled bidding rule | Explicit `public_sector_disabled` notice; 8.99 seconds including preview navigation |
| 3861744322 | 15346471 | Generic timeout; Discovery explicitly reported the event closed | Explicit `closed` notice; 8.14 seconds including preview navigation |
| 3949177873 | 15889645 | The earlier production nightly observed a permission denial; current baseline captured its files | 3 files captured; correct Doc3949177873 Sourcing header |
| 3949149442 | 15889504 | Existing hardcoded training exclusion, but direct live capture succeeded | 3 files captured; correct Doc3949149442 Sourcing header |
| 4044346425 | 16516115 | Existing hardcoded exclusion; first audit received a permission/invalid-link message | Later normal runs captured 3 files; correct Doc4044346425 Sourcing header |

The source's public metadata labelled all five Published with 2099 deadlines and `isTest=false`.
Those fields do not establish Sourcing availability or a safe reason to suppress capture.
The two original rejections were recognized immediately once their visible message state
landed, rather than consuming the old 60-second post-Respond wait.

Public/login preview routes use this shape:
`https://portal.us.bn.cloud.ariba.com/dashboard/appext/comsapsbncdiscoveryui#/RfxEvent/preview/<posting>`.
Only document/posting identities, sanitized messages and capture hashes are published here.
Account data, credentials and authenticated session query strings remain outside the repo.

The first message identifies a source bidding rule; the second identifies a source-closed
state. Their underlying buyer/SAP configuration and whether the publisher intends permanent
unavailability were not established. Similarly, the permission message itself allows an
invalid-link explanation. Access outcomes changed between ordinary visits for other training
events; the specific cause of that variation is unverified. No alternate account, access
restriction bypass, prerequisite acceptance or bid submission was used.

## Behavior change

Remove the early training blacklist. Every pending event now receives the normal capture
attempt, and supported training events are archived normally. `VERIFIED_TRAINING_EVENTS`
records the five reviewed document/posting pairs; it does not remove them from selection.

After Respond, `_wait_post_respond` recognizes the visible Discovery rejection heading together
with its specific public-sector-rule or closed message. It also distinguishes the existing
Sourcing permission/invalid-link message and anonymous Register/Login view. NBSP whitespace is
normalized. Generic text, a training title, a distant date or an unknown error cannot establish
one of the recognized restrictions.

Explicit source rejections raise `EventUnavailable`. Only a recognized rejection paired with
an exact reviewed training identity becomes a `training_restrictions` notice. It stays
retryable on subsequent runs and never creates a canonical archive. If access later works,
the normal document capture path runs. Real procurement rejections, mismatched posting IDs,
anonymous sessions and unexpected loading failures remain in the failure channel. In
particular, real-event permission denials now mark the nightly failed rather than returning
a clean skip.

Existing omission/collision evidence still takes precedence over a training notice: held gaps
remain failures and the bundle, gap record and index are preserved. The nightly displays the
restriction count in its attachment-step detail and names each restriction in a separate
Training event restrictions section. Real failures still reach both the Failures section and
`sync_run` history. Explicit known training restrictions alone do not mark the run failed.

## Capture verification and recovery

The corrected capture ran through the real selection, browser, download and indexing flow
against a copy of the production database, restricted to the five reviewed training postings.
The final run had **3 captures, 2 restriction notices, 0 failures**. A diagnostic wrapper
checked the actual Sourcing document header before constructing every file source, so a
stale/wrong event could not validate the recovered bundles.

Each supported event holds `Placeholder.docx`, `Placeholder_2.docx` and `Placeholder_3.docx`:

| Canonical bundle | Bytes | Indexed files | SHA-256 |
| --- | ---: | ---: | --- |
| Doc3949149442.zip | 25,944 | 3 | `eb42d02293fe6fb874a123784698406dba570cf5875ff6962d0c9b4a9772e532` |
| Doc3949177873.zip | 25,944 | 3 | `eb42d02293fe6fb874a123784698406dba570cf5875ff6962d0c9b4a9772e532` |
| Doc4044346425.zip | 25,944 | 3 | `eb42d02293fe6fb874a123784698406dba570cf5875ff6962d0c9b4a9772e532` |

The three training templates produced identical placeholder bytes, but their source identities
were checked separately and they remain separately indexed. All zips passed CRC verification
and had no recorded omissions or collisions.

All three new bundles are now in `/home/alex/tb-data/ariba/attachments/` and their nine files
are indexed in production. No canonical path or prior rows existed for these documents before
promotion. The database was backed up first to `/tmp/tb-242/bids.before-recovery.sqlite`; all
2,684 existing attachment rows and all five posting records were verified unchanged afterward.
The unavailable original two events retain their posting metadata and have no invented bundle.

Durable sanitized evidence and duplicate recovery bytes are held at:
`/home/alex/tb-data/ariba/_242_recovery_20261020/`.
No extraction, paid model calls, full nightly, export or publication ran during recovery.

## Regression coverage

**953 offline tests passed.** New tests cover exact message states and NBSP formatting,
supported training capture (including a previously restricted identity), real-event denials,
unknown/session errors, identity mismatches, preserved attachment gaps, selection from both
source keyspaces, and nightly notice/failure reporting with durable real-error history.
The original unit assertions that permanently excluded training IDs or silently skipped
real permission denials were updated after live evidence disproved those policies.

Sanitized source-message fixtures and their provenance are in `scrapers/tests/fixtures/ariba/`.
