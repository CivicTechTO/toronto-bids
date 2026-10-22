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
