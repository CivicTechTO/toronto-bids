# Bonfire portal fixtures

Unmodified anonymous HTTP responses captured under maintainer permission. No credentials, cookies, or request headers are stored. Raw responses include `PrivateProjectID` fields, which normalization ignores (only public `ProjectID` URLs are used).

## Toronto Public Library (#110, 2026-10-22)

- `library-portal.html`: `https://torontopubliclibrary.bonfirehub.ca/portal/?tab=openOpportunities`
- `library-open.json`: `https://torontopubliclibrary.bonfirehub.ca/PublicPortal/getOpenPublicOpportunitiesSectionData`
- `library-past.json`: `https://torontopubliclibrary.bonfirehub.ca/PublicPortal/getPastPublicOpportunitiesSectionData`

Linked from `https://tpl.ca/about-the-library/business-with-the-library/`. Status constants verified against `https://assets.bonfirehub.com/100685/js/bonfire/bonfireConstants.js`; `BFUtil.createMomentFromUtcDateString` confirms UTC date handling. Contains 1 Open and 411 Past records (all `IsPublicAward = false`), including repeated printed references on distinct project pages.

## Shared TCHC / TSHC portal (#107, 2026-10-23 16:29 UTC)

Captured from `https://torontohousing.bonfirehub.ca`:

- `housing-portal.html` (`89d7db81c6103899c388b6b7c5562d0a80f416e8d31b388e993bdedefc1f71c0`)
- `housing-open.json` (`7f9f730c6b6b3932e917aad8d06daefdb968bc023178f1363b3fa700c672c518`)
- `housing-past.json` (`4661f9171af71523de4dfa259d310e6bdb06ec122e9c584c195852e253d55b94`)

Contains 16 Open and 945 Past postings (normalizing to 16 Open, 629 Awarded, 226 Closed, 90 Cancelled; earliest close date 2019-05-21). Department labels distinguish Toronto Community Housing, Toronto Seniors Housing, and TCHC/TSHC.

## Waterfront, Exhibition Place, and TAF (#134, 2026-10-24)

| Fixture | Source URL | SHA-256 |
|---|---|---|
| `waterfront-portal.html` | `https://waterfrontoronto.bonfirehub.ca/portal/?tab=openOpportunities` | `50dd395eadff215bbab40c19a6f1b746c6503f46b830512e87566e7c93ef334a` |
| `waterfront-open.json` | `https://waterfrontoronto.bonfirehub.ca/PublicPortal/getOpenPublicOpportunitiesSectionData` | `5f4a2720c4084b253e05f401b5e4725e27899b9bb774e084b668d39a6dd15e43` |
| `waterfront-past.json` | `https://waterfrontoronto.bonfirehub.ca/PublicPortal/getPastPublicOpportunitiesSectionData` | `bfdb8ed8ec39c458d4d6dd2ec0ec8b6c25c9b067698073795004770dff21e9f2` |
| `exhibition-portal.html` | `https://explace.bonfirehub.ca/portal/?tab=openOpportunities` | `7450a3218be1d89a7db601679d05eb0837fda4088fb8d94010f29d521691bfb4` |
| `exhibition-open.json` | `https://explace.bonfirehub.ca/PublicPortal/getOpenPublicOpportunitiesSectionData` | `faae77147156dbe5a9d9e555fd1d4134b780bcfebadb47a18d70cdee62ae397e` |
| `exhibition-past.json` | `https://explace.bonfirehub.ca/PublicPortal/getPastPublicOpportunitiesSectionData` | `299bf20a650de50733ca42ce3aafbbe97799c23acd90f134b96bbf480c5f33cf` |
| `taf-portal.html` | `https://taf.bonfirehub.ca/portal/?tab=openOpportunities` | `560564652ebc5e5189f0985675b113d93742238a105b3ed217962890e3855602` |
| `taf-open.json` | `https://taf.bonfirehub.ca/PublicPortal/getOpenPublicOpportunitiesSectionData` | `15633c14378754a41193e203d6ad19a187354f52b3e2c0df051ae5a803dfc12a` |
| `taf-past.json` | `https://taf.bonfirehub.ca/PublicPortal/getPastPublicOpportunitiesSectionData` | `31797d30981e9f3f99d703da38e090b517062f4c487edbe67af0b162495c36b8` |

- **Waterfront** (listed under its legal name, Toronto Waterfront Revitalization Corporation): 78 postings (1 Open, 77 Closed).
- **Exhibition Place**: 94 postings (1 Open, 77 Awarded, 12 Cancelled, 4 Closed), including two distinct pages labelled `EPRFP2025-7745`.
- **TAF**: 0 Open (empty collection) and 16 Past postings (8 Awarded, 6 Closed, 2 Cancelled).
