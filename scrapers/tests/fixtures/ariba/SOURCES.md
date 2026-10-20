# Ariba training event outcome fixtures (#242)

Captured from the visible authenticated Discovery result on 2026-10-20 after the ordinary
Respond action, using the PMMD-authorized archival supplier account:

| Fixture | Document / posting | Observation |
| --- | --- | --- |
| training_public_sector_disabled.txt | 3861692164 / 15346406 | Discovery rejection heading plus public-sector bidding-rule message |
| training_closed.txt | 3861744322 / 15346471 | Discovery rejection heading plus explicit event-closed message |

The fixtures contain only the two message lines, with NBSP whitespace normalized. Tests
restore NBSP to exercise the live formatting. No account names, credentials, session URLs,
request headers or private form data are included. These are current source rejections, not
proof that the publisher intends permanent unavailability. No training title, future deadline
or API `isTest` value establishes either outcome.

Authenticated preview routes (login may be required):

- https://portal.us.bn.cloud.ariba.com/dashboard/appext/comsapsbncdiscoveryui#/RfxEvent/preview/15346406
- https://portal.us.bn.cloud.ariba.com/dashboard/appext/comsapsbncdiscoveryui#/RfxEvent/preview/15346471
