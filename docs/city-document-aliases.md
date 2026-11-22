# Published City document aliases

During the City's transition to Ariba, some original City call pages printed both a native Call Number and an Ariba Doc number on the same page. `linking/city_document_aliases.py` applies reviewed same-page aliases from `data/city_document_aliases.json` (which includes the archived HTML bytes and URLs so resolution runs offline without network or LLM calls).

## Admission requirements

An alias entry is accepted only when all of the following hold:

1. The printed Call Number matches an independently validated archived City award (exact full supplier name, printed award date, and amount uniquely matching one record across the frozen and current publisher populations).
2. The call page's Description field explicitly names one unambiguous Ariba Doc number (for example `See ARIBA Doc`, `Ariba Doc No.`, or `Ariba Reference No. Doc`). Repeated identical IDs count as one; multiple different IDs or cancellation/supersession text are rejected. Filenames, digit conversions, and mentions outside the call page do not qualify.
3. The call page's issue and closing dates match the current publisher record.
4. No second current City solicitation exists for the alternate Doc number (otherwise it requires manual review).

## Resolution and export behavior

- All aliases are validated before any merge runs. `city_document_alias_evidence` stores the immutable approval and digest. Any changed proof, source bytes, publisher identity, or conflicting identifier fails resolution and rolls back via savepoint.
- Only validated pairs cross the resolver's single-City-document anchor guard. The main City solicitation keeps its canonical ID and the alternate identity redirects to it.
- Existing bid IDs, document numbers, council references, prices, statuses, and `first_seen` timestamps stay unchanged. Aliases do not add bids or merge suppliers.
- Coverage audits read `city_document_alias_evidence` directly and check that canonical attribution agrees.
- JSON exports nest the bids and report under the City solicitation while keeping each bid's original alternate document number. The approval table is included in JSON, CSV, and Parquet exports.

## Example

The first reviewed alias is [call 3721-19-0159](https://web.archive.org/web/20190615041306id_/https://wx.toronto.ca/inter/pmmd/calls.nsf/0/A82A07B374C7DA89852583F6006A9D14?OpenDocument), which prints both `Call number: 3721-19-0159` and `See ARIBA Doc 1745310659`. [BA40.8's staff report](https://www.toronto.ca/legdocs/mmis/2019/ba/bgrd/backgroundfile-136556.pdf) provides the three submitted bidders. (The report's second award amount is rounded relative to the feed and is not used for identity or to alter stored source values.)
