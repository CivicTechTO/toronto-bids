# Automatic EP/Zoo council-term rollover (#225)

The previous discovery loop only walked configured terms. Both active boards ended at
2022–2026, so a published `2027.EP1` or `2027.ZB1` was never requested. Adding a speculative
term by hand also made the previous term eligible for a permanent cached miss boundary.
An offline reproduction confirmed that a supplied 2027 first agenda was missed.

EP and Zoo now opt into automatic four-year extensions from their latest seed. Discovery
probes each prospective first agenda through the current calendar year. A recognized agenda
with a date within the prospective term confirms that term. A successor meeting must also
have occurred before the older term can close: prepublished future agendas are collected
without freezing its tail. Historical BA/BD consumers keep their existing behavior.

Closed-term cache records now carry the successor reference and date alongside `last_n`.
Old integer caps are revalidated once. Unknown/block responses raise instead of confirming a
term. Missing responses are not cached; malformed or wrong-term cached pages are refetched.
The latest term remains open even if a stale closure record exists.

## Validation

- Full offline suite: **906 passed**. Fifteen new cases cover discovery, repeated rollover,
  premature closure, stale boundaries, persistence, wrapper configuration, and invalid pages.
- Live headed Chromium probe on 2026-10-20, using the normal TMMIS fetcher:

| Reference | Result |
| --- | --- |
| 2023.EP1 | Agenda dated 2022-12-09 |
| 2027.EP1 / 2028.EP1 | Recognized missing responses |
| 2023.ZB1 | Agenda dated 2022-12-08 |
| 2027.ZB1 / 2028.ZB1 | Recognized missing responses |

Neither successor term was confirmed, so neither current term became eligible for closure.
A real published 2026–2030 agenda cannot yet be tested; offline cases exercise that transition.
The change assumes the current four-year term cycle, November term boundary, and numbering
restart continue. No production database, agenda cache, or paid extraction was changed.
