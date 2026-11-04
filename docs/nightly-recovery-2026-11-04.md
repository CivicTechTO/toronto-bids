# November 4 nightly recovery

Publication stopped at a GitHub release asset API HTTP 502 after successful export. The multi-asset clobber had updated some files, leaving a mixed rolling release. The release was republished; GitHub and R2 SQLite downloads matched SHA-256 `5433eae9237ba793997616c57aba5ffc80619b48c306caaf7f2445a87d1376be`. [Frontend run 41874088440](https://github.com/CivicTechTO/toronto-bids-frontend/actions/runs/41874088440) succeeded, followed by independent live-page verification.

Merged #291 was deployed after rechecking all 130 source proofs against the current original OData population. Awarded City solicitations without attributed bids fell from 3,939 to 3,809.

The publisher now retries explicit HTTP 5xx asset failures at most three times with 5/10-second backoff, reusing the same complete clobber arguments. The manifest uploads after the data files succeed. Authentication errors and rate limits do not trigger these retries. Publication remains non-atomic during rolling uploads.

## Ariba

Doc5935859773 (RFX1110030461), posted November 3 and closing December 2, succeeded in a fresh ordinary authenticated session. Its six-file bundle was checked against the actual event identity, indexed and republished. ZIP SHA-256: `128a2a4d85097a857bb5f07b8bcd5c7ab4ed9db2cb8ec6ec63c5f2effa2b5461`.

The earlier denial therefore does not establish a permanent restriction. Nightly tries one fresh authenticated context after an explicit permission denial. A repeated denial remains visible and retryable; other rejection classes retain their existing handling.

## TRCA

Direct HTTP returned identical eSCRIBE “Verifying your browser” HTTP 403 HTML for new document21060 and known-public control20515. One ordinary anonymous headed Chromium calendar session returned valid exact-original PDFs for the control and all seven failed new URLs. No account or special verification bypass was needed.

All seven are linked from the [November 6 Board agenda](https://pub-trca.escribemeetings.com/Meeting.aspx?Id=988cf12c-877f-4c7a-a551-7db65ca36141&Agenda=Agenda&lang=English). The calendar reported `MeetingPassed=false`; no minutes or decision document was published in that entry.

| DocumentId | Source | PDF SHA-256 |
| --- | --- | --- |
| 21060 | Item8.10, Canyon Avenue RFT10044924 | `8e123370af86f6a97b90b30ad0ae03d6fc8c432f7bc0d7fc1f6a1d0b3ffe7632` |
| 21061 | Item8.10 attachment1 | `c39b29fc8d3834b2cc1546e2f0a3dd3b9f65fe4bb418233bd2e69a530486e6b9` |
| 21062 | Item8.10 attachment2 | `ec062bc83abfcdc174e0957232e3777444b36264c8b9752a6be3d1d313a25752` |
| 21063 | Item8.11, East Highland Creek RFT10045110 | `5dd0510d4611b1f2fe9957eada35cc8c6a14a5c71cdd139cbdf22d006fd6ed0f` |
| 21064 | Item8.11 attachment1 | `6d0eb4cc71248072b144118ee549ea16d3eda440286cadf9a519fd7c5ed0b82b` |
| 21045 | Item9.1, Q3 communications | `817b8280f53c0a17d00a86097ca339e54bf95574207c290ace92a69114dd05db` |
| 21031 | Item10.1, delegated permits | `8a10fbb7acd65fb20da6e30c9f85e23ef4ebda14a69ac4a9a159b1dc71d1aa17` |

Nightly uses one lazy browser session, serial requests with at least one-second pacing, strict exact-host/document identity, a byte limit and PDF structural validation. Remaining verification pages or genuine denials stay queued and failed; rate limits or Retry-After stop the pass. These seven recoverable originals were not added to the reviewed unavailable registry. Older reviewed originals retain their own evidence and handling.

Item8.10 distinguishes six actual submissions from other procurement stages, including two non-compliant submissions without disclosed numeric prices. Item8.11 has three actual submissions after five firms prequalified. Both recommend awards to a future meeting; capture does not establish adoption.

## Decision review and replay

`background_pdf.award_review_context_json` preserves the original calendar/meeting record for reports discovered under `MeetingPassed=false`. The hold survives rediscovery and follows exact PDF hashes across indexed URLs. Backfill retains supported bids and original award claims in `agency_derivation`, but withholds derived awards and awarded status. Calendar passage cannot release the hold.

This guard is deliberately conservative: earlier award references within a future agenda report are also held until contract-level review. Replaying the actual agenda and seven captured PDFs on a production database copy retained all 8,723 bid rows and all 3,096 extraction caches, but moved ten existing award rows into review (3,602 to 3,592). Those claims span documents21022,21030,21037,21044; they include prior awards and proposed extensions/changes. They have not all been established as invalid. No production TRCA rows were changed by this replay.

The original #292 guard had no release mechanism. The follow-on [claim review](trca-decision-source-review-2026-11-04.md) binds carried decision evidence to exact report bytes and individual contracts. Calendar passage still cannot release a claim; do not erase context or alter raw extraction caches to bypass the guard.

After #292 merged, all seven originals were archived in production with this guard. The two procurement reports completed batched extraction and added nine tender bids; the other five documents were excluded by the existing classifier. Source responses, agenda/calendar, exact hashes, text, deployment state and replay records are retained on the server under `.validation-nightly-2026-11-04/`. Investigation and replay made no paid model requests.
