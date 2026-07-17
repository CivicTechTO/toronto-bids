import os
from pathlib import Path

# Data directory: scrapers/files/ by default, overridable for tests / deployment.
DATA_DIR = Path(os.environ.get("TB_DATA_DIR", Path(__file__).resolve().parent.parent / "files"))
DB_PATH = DATA_DIR / "bids.sqlite"

CKAN_BASE = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/"
ODATA_BASE = "https://secure.toronto.ca/c3api_data/v2/DataAccess.svc/pmmd_solicitations/"

# CKAN dataset slugs (resource UUIDs are resolved at runtime, never hardcoded).
CKAN_AWARDED_SLUG = "tobids-awarded-contracts"
CKAN_OPEN_SLUG = "tobids-all-open-solicitations"
CKAN_NONCOMP_SLUG = "tobids-non-competitive-contracts"
# Forward-looking: solicitations the City intends to issue (#69). No document_number yet.
CKAN_PIPELINE_SLUG = "capital-project-pipeline"

# OData entity sets.
ODATA_SOLICITATIONS = "feis_solicitation_published"
ODATA_NONCOMPETITIVE = "feis_non_competitive_published"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
HTTP_TIMEOUT = 60.0
HTTP_RETRIES = 4

# SAP Ariba Discovery public JSON APIs (no auth).
ARIBA_SEARCH_URL = "https://service.ariba.com/Network/discoveryweb/search/public/v1/doIndexedSearch"
ARIBA_DETAIL_URL = "https://service.ariba.com/Network/discoveryweb/api/public/v1/rfx/{rfx_id}"
ARIBA_SEARCH_PARAMS = {"siteName": "Quote"}
ARIBA_SEARCH_BODY = {
    "pageSize": 1000,
    "pageNum": 0,
    "searchType": "Quote",
    "sortBy": "RESPONSE_DEAD_LINE",
    "filters": [],
}
ARIBA_CUSTOMER_NAME = "City of Toronto"

# Suspended & Disqualified Firms registry (public HTML, no auth).
SUSPENDED_FIRMS_URL = (
    "https://www.toronto.ca/business-economy/doing-business-with-the-city/"
    "searching-bidding-on-city-contracts/suspended-disqualified-firms/"
)

# TMMIS council agenda-item pages (Akamai-gated -> headed browser). Query param: ?item=<reference>.
COUNCIL_ITEM_URL = "https://secure.toronto.ca/council/agenda-item.do"
# Downloaded council PDFs.
COUNCIL_DOCS_DIR = DATA_DIR / "documents" / "council"
# Award Summary Forms from the Toronto Bids Portal (#114). Kept apart from the council
# documents: different publisher, different provenance, and they are the only bid source
# after the Bid Award Panel was abolished on 2025-10-01.
AWARD_SUMMARY_DIR = DATA_DIR / "documents" / "award_summary"

# Raw Bid Award Panel agenda HTML. Kept because fetching is the Akamai-gated, browser-bound,
# expensive half (475 pages, ~10 min) while parsing is free and repeatable — and the same
# pages carry the supplier/amount/Call Number that the pre-Ariba years need (#77).
COUNCIL_AGENDAS_DIR = DATA_DIR / "council" / "agendas"

# Archived Ariba posting pages from the legacy rescue; their <title> is the real
# solicitation title (#65).
LEGACY_ARIBA_DIR = DATA_DIR / "legacy" / "azure" / "ariba_data"
