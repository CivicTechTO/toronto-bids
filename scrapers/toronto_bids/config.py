import os
from pathlib import Path

# Load scrapers/.env if python-dotenv is installed (it ships with the `council` extra). Guarded
# so the core pipeline needs no new dependency; without it, credentials just come from the real
# environment. Keyed to the package's own parent so it works regardless of the caller's cwd.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

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

# Solicitation document bundles behind Ariba's "Respond" gate (#117), one zip per event named
# by document number. Bytes only — the DB holds the index (ariba_attachment). Not committed.
ARIBA_ATTACHMENTS_DIR = DATA_DIR / "ariba" / "attachments"
# The supplier account the attachment scraper logs in as. Read from the gitignored scrapers/.env
# (the repo is public — credentials never go in it). Respond is authorized by PMMD (#117), but
# a bid is never submitted.
ARIBA_LOGIN_URL = "https://service.ariba.com/Supplier.aw/109590048/aw?awh=r&awssk=login"
ARIBA_USERNAME = os.environ.get("ARIBA_USERNAME")
ARIBA_PASSWORD = os.environ.get("ARIBA_PASSWORD")

# TRCA meeting records (#135): current record on eSCRIBE, back-catalogue agenda packages
# on TRCA's Laserfiche. Both are TRCA's own hosting (open-data licence) — NOT the
# bids&tenders portal, which stays gated.
TRCA_ESCRIBE_BASE = "https://pub-trca.escribemeetings.com/"
TRCA_REPORTS_DIR = DATA_DIR / "agencies" / "trca"
# The eSCRIBE calendar is client-rendered from this ASP.NET page-method (JSON); the year
# landing page carries no static meeting links, so discovery POSTs here per year (#137).
TRCA_CALENDAR_URL = TRCA_ESCRIBE_BASE + "MeetingsCalendarView.aspx/GetCalendarMeetings"
# eSCRIBE years to fetch; range() endpoint updated by whoever runs it in 2027 — moot
# then anyway (Bill 97 amalgamates TRCA 2027-02-01).
TRCA_ESCRIBE_YEARS = range(2019, 2028)

# Toronto Zoo Board of Management (#135): the ZB committee on TMMIS, same infrastructure
# as the Bid Award Panel (agendas need a headed browser; report PDFs are plain-HTTP legdocs).
ZOO_AGENDAS_DIR = DATA_DIR / "agencies" / "zoo" / "agendas"
ZOO_REPORTS_DIR = DATA_DIR / "agencies" / "zoo"

# bids&tenders portals (#135). `enabled` stays False until the BODY's written permission
# is recorded in docs/permissions/ and the flipping commit references it — the PMMD/Ariba
# precedent (#117). The Vendor ToS is clickwrap we have not accepted, and its copyright
# notice is blanket; "settled" means the body said yes, not our reading of their terms.
BIDS_TENDERS_PORTALS = [
    {"slug": "toronto-zoo", "portal_url": "https://torontozoo.bidsandtenders.ca/",
     "enabled": True, "permission": "docs/permissions/2026-07-18-toronto-zoo.md"},
    {"slug": "trca", "portal_url": "https://trca.bidsandtenders.ca/",
     "enabled": True, "permission": "docs/permissions/2026-07-18-trca.md"},
]

# Raw bids&tenders listing JSON captured by `--record`, one file per record — the seed for
# real parser fixtures once a portal has data (#135).
PORTAL_RECORDINGS_DIR = DATA_DIR / "agencies" / "portal_recordings"
