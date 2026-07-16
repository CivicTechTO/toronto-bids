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
