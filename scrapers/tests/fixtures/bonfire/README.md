Actual anonymous responses captured 2026-10-22 for #110 under the maintainer's
explicit Bonfire permission. No records were invented or rewritten.

- `library-portal.html`: https://torontopubliclibrary.bonfirehub.ca/portal/?tab=openOpportunities
- `library-open.json`: https://torontopubliclibrary.bonfirehub.ca/PublicPortal/getOpenPublicOpportunitiesSectionData
- `library-past.json`: https://torontopubliclibrary.bonfirehub.ca/PublicPortal/getPastPublicOpportunitiesSectionData

The Library's own page https://tpl.ca/about-the-library/business-with-the-library/
links the portal. Its public JavaScript and BFUtil.loadSection identify these GET
requests; BFUtil.createMomentFromUtcDateString establishes UTC date semantics.
Status constants were checked against the same portal build at
https://assets.bonfirehub.com/100685/js/bonfire/bonfireConstants.js.
The capture contains 1 Open and 411 Past records, including repeated printed
references on separate project pages. All captured IsPublicAward values are false.
PrivateProjectID fields are present in the public response; tests retain original
bytes, but normalization only follows public ProjectID URLs and never uses those
private-route identifiers. No credentials, cookies or request headers are retained.


## Shared TCHC/TSHC portal (#107)

`housing-portal.html`, `housing-open.json` and `housing-past.json` are unchanged
anonymous response bodies captured 2026-10-23 16:29 UTC from the corresponding
landing/Open/Past endpoints on `https://torontohousing.bonfirehub.ca`.
TCHC's official Become a vendor page links this portal. Source response hashes:

- landing: `89d7db81c6103899c388b6b7c5562d0a80f416e8d31b388e993bdedefc1f71c0`
- Open: `7f9f730c6b6b3932e917aad8d06daefdb968bc023178f1363b3fa700c672c518`
- Past: `4661f9171af71523de4dfa259d310e6bdb06ec122e9c584c195852e253d55b94`

There are 16 Open and 945 Past postings; at the captured instant these normalize to
16 Open, 629 Awarded, 226 Closed and 90 Cancelled. Earliest closing date is 2019-05-21.
The Awarded label is only a public portal badge, not a supplier/value award fact.
Public department labels distinguish Toronto Community Housing, Toronto Seniors
Housing and TCHC/TSHC; the buyer profile explicitly groups their shared portal.
No credentials, cookies or request headers are included. As in the Library fixture,
public response identifiers are retained but never used for private-route requests.
