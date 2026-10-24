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


## Remaining #134 portals — 2026-10-24

The `waterfront-*`, `exhibition-*` and `taf-*` fixtures are unchanged anonymous
landing/Open/Past response bodies from the official linked portals listed below.
The landing files preserve original source whitespace. No credentials, cookies
or request headers are included; public identifiers are retained but never
followed to private routes.

| Fixture | Source URL | SHA-256 |
| --- | --- | --- |
| `waterfront-portal.html` | `https://waterfrontoronto.bonfirehub.ca/portal/?tab=openOpportunities` | `50dd395eadff215bbab40c19a6f1b746c6503f46b830512e87566e7c93ef334a` |
| `waterfront-open.json` | `https://waterfrontoronto.bonfirehub.ca/PublicPortal/getOpenPublicOpportunitiesSectionData` | `5f4a2720c4084b253e05f401b5e4725e27899b9bb774e084b668d39a6dd15e43` |
| `waterfront-past.json` | `https://waterfrontoronto.bonfirehub.ca/PublicPortal/getPastPublicOpportunitiesSectionData` | `bfdb8ed8ec39c458d4d6dd2ec0ec8b6c25c9b067698073795004770dff21e9f2` |
| `exhibition-portal.html` | `https://explace.bonfirehub.ca/portal/?tab=openOpportunities` | `7450a3218be1d89a7db601679d05eb0837fda4088fb8d94010f29d521691bfb4` |
| `exhibition-open.json` | `https://explace.bonfirehub.ca/PublicPortal/getOpenPublicOpportunitiesSectionData` | `faae77147156dbe5a9d9e555fd1d4134b780bcfebadb47a18d70cdee62ae397e` |
| `exhibition-past.json` | `https://explace.bonfirehub.ca/PublicPortal/getPastPublicOpportunitiesSectionData` | `299bf20a650de50733ca42ce3aafbbe97799c23acd90f134b96bbf480c5f33cf` |
| `taf-portal.html` | `https://taf.bonfirehub.ca/portal/?tab=openOpportunities` | `560564652ebc5e5189f0985675b113d93742238a105b3ed217962890e3855602` |
| `taf-open.json` | `https://taf.bonfirehub.ca/PublicPortal/getOpenPublicOpportunitiesSectionData` | `15633c14378754a41193e203d6ad19a187354f52b3e2c0df051ae5a803dfc12a` |
| `taf-past.json` | `https://taf.bonfirehub.ca/PublicPortal/getPastPublicOpportunitiesSectionData` | `31797d30981e9f3f99d703da38e090b517062f4c487edbe67af0b162495c36b8` |

Waterfront's title is Toronto Waterfront Revitalization Corporation, its legal name;
its fixture contains 78 postings (1 Open, 77 Closed). Exhibition Place contains
94 (1 Open, 77 Awarded, 12 Cancelled, 4 Closed), including two distinct pages
labelled EPRFP2025-7745. TAF contains 16 past postings (8 Awarded, 6 Closed,
2 Cancelled) and an explicit empty Open collection. Badge labels do not identify
winning suppliers or contract values.
