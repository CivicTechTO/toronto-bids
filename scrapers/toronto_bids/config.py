import os
from pathlib import Path

# Load scrapers/.env relative to the package root if python-dotenv is installed.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

DATA_DIR = Path(
    os.environ.get("TB_DATA_DIR", Path(__file__).resolve().parent.parent / "files")
)
DB_PATH = DATA_DIR / "bids.sqlite"

CKAN_BASE = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/"
ODATA_BASE = (
    "https://secure.toronto.ca/c3api_data/v2/DataAccess.svc/pmmd_solicitations/"
)

# CKAN dataset slugs (resource UUIDs are resolved at runtime).
CKAN_AWARDED_SLUG = "tobids-awarded-contracts"
CKAN_OPEN_SLUG = "tobids-all-open-solicitations"
CKAN_NONCOMP_SLUG = "tobids-non-competitive-contracts"
CKAN_PIPELINE_SLUG = "capital-project-pipeline"

ODATA_SOLICITATIONS = "feis_solicitation_published"
ODATA_NONCOMPETITIVE = "feis_non_competitive_published"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
HTTP_TIMEOUT = 60.0
HTTP_RETRIES = 4

# Anonymous SAP Business Network Discovery APIs.
ARIBA_SEARCH_URL = (
    "https://portal.us.bn.cloud.ariba.com/tpx/ingress/tps/discovery-core/public/rfx/v1/search"
)
ARIBA_DETAIL_URL = (
    "https://portal.us.bn.cloud.ariba.com/tpx/ingress/tps/discovery-core/odata/public/"
    "discovery/v1/rfx/RfxEvent('{rfx_id}')"
)
ARIBA_ORG_ANID = "AN01050912625"
ARIBA_SEARCH_PAGE_SIZE = 100
ARIBA_CUSTOMER_NAME = "City of Toronto"

SUSPENDED_FIRMS_URL = (
    "https://www.toronto.ca/business-economy/doing-business-with-the-city/"
    "searching-bidding-on-city-contracts/suspended-disqualified-firms/"
)

# TMMIS agenda-item endpoint (?item=<reference>); Akamai-gated, requires headed browser.
COUNCIL_ITEM_URL = "https://secure.toronto.ca/council/agenda-item.do"
COUNCIL_DOCS_DIR = DATA_DIR / "documents" / "council"
AWARD_SUMMARY_DIR = DATA_DIR / "documents" / "award_summary"

COUNCIL_AGENDAS_DIR = DATA_DIR / "council" / "agendas"

COMMITTEE_ITEMS_DIR = DATA_DIR / "council" / "committee_items"

LEGACY_ARIBA_DIR = DATA_DIR / "legacy" / "azure" / "ariba_data"

ARIBA_ATTACHMENTS_DIR = DATA_DIR / "ariba" / "attachments"
ARIBA_LOGIN_URL = "https://service.ariba.com/Supplier.aw/109590048/aw?awh=r&awssk=login"
# Treat checked-in .env placeholders as unset so credential checks fail before Playwright login.
_ARIBA_PLACEHOLDER = {
    "ARIBA_USERNAME": "your-ariba-supplier-username",
    "ARIBA_PASSWORD": "your-ariba-supplier-password",
}


def _real_env(name: str) -> str | None:
    value = os.environ.get(name)
    return None if value == _ARIBA_PLACEHOLDER.get(name) else value


ARIBA_USERNAME = _real_env("ARIBA_USERNAME")
ARIBA_PASSWORD = _real_env("ARIBA_PASSWORD")

TRCA_ESCRIBE_BASE = "https://pub-trca.escribemeetings.com/"
TRCA_REPORTS_DIR = DATA_DIR / "agencies" / "trca"
TRCA_HISTORY_DIR = DATA_DIR / "agencies" / "trca_history"
# ASP.NET JSON endpoint for client-rendered eSCRIBE calendar.
TRCA_CALENDAR_URL = TRCA_ESCRIBE_BASE + "MeetingsCalendarView.aspx/GetCalendarMeetings"
TRCA_ESCRIBE_YEARS = range(2019, 2028)

ZOO_AGENDAS_DIR = DATA_DIR / "agencies" / "zoo" / "agendas"
ZOO_REPORTS_DIR = DATA_DIR / "agencies" / "zoo"

# Only enable portals with written permission in docs/permissions/.
# export/document.py lifts enabled portals' attribution strings into meta.attribution.
BIDS_TENDERS_PORTALS = [
    {
        "slug": "toronto-zoo",
        "body": "Toronto Zoo",
        "portal_url": "https://torontozoo.bidsandtenders.ca/",
        "enabled": True,
        "permission": "docs/permissions/2026-07-18-toronto-zoo.md",
        "attribution": "Source: Toronto Zoo (torontozoo.bidsandtenders.ca).",
    },
    {
        "slug": "trca",
        "body": "Toronto and Region Conservation Authority (TRCA)",
        "portal_url": "https://trca.bidsandtenders.ca/",
        "enabled": True,
        "permission": "docs/permissions/2026-07-18-trca.md",
        "attribution": (
            "Source: Toronto and Region Conservation Authority (trca.bidsandtenders.ca)."
        ),
    },
]

# Raw bids&tenders snapshots; --record also writes empty captures.
PORTAL_RECORDINGS_DIR = DATA_DIR / "agencies" / "portal_recordings"

EP_AGENDAS_DIR = DATA_DIR / "agencies" / "ep" / "agendas"
EP_REPORTS_DIR = DATA_DIR / "agencies" / "ep"

TCHC_PAST_MEETINGS_URL = (
    "https://torontohousing.ca/board-and-governance/past-board-and-committee-meetings"
)
TCHC_MEETINGS_DIR = DATA_DIR / "agencies" / "tchc" / "meetings"
TCHC_REPORTS_DIR = DATA_DIR / "agencies" / "tchc"
TCHC_MIN_YEAR = 2021

TPL_MEETINGS_URL = "https://tpl.ca/about-the-library/board/meetings/"
TPL_MEETINGS_DIR = DATA_DIR / "agencies" / "tpl" / "meetings"
TPL_REPORTS_DIR = DATA_DIR / "agencies" / "tpl"
TPL_MIN_YEAR = 2014

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLASSIFICATION_LABELS_PATH = (
    _REPO_ROOT
    / "docs"
    / "machine-labels"
    / "document-classification"
    / "labels-machine.json"
)

# Bonfire portals (anonymous listing metadata only; permissions in docs/permissions/).
BONFIRE_PORTALS = [
    {
        'slug': 'toronto-public-library',
        'body': 'Toronto Public Library',
        'portal_url': 'https://torontopubliclibrary.bonfirehub.ca',
        'enabled': True,
        'permission': 'docs/permissions/2026-10-22-bonfire.md',
        'attribution': 'Source: Toronto Public Library (torontopubliclibrary.bonfirehub.ca).',
    },
    {
        'slug': 'toronto-community-housing',
        'body': 'Toronto Community Housing and Toronto Seniors Housing',
        'portal_url': 'https://torontohousing.bonfirehub.ca',
        'enabled': True,
        'permission': 'docs/permissions/2026-10-22-bonfire.md',
        'attribution': 'Source: Toronto Community Housing and Toronto Seniors Housing '
                       '(torontohousing.bonfirehub.ca).',
    },
    {
        'slug': 'waterfront-toronto',
        'body': 'Toronto Waterfront Revitalization Corporation',
        'portal_url': 'https://waterfrontoronto.bonfirehub.ca',
        'enabled': True,
        'permission': 'docs/permissions/2026-10-22-bonfire.md',
        'attribution': 'Source: Toronto Waterfront Revitalization Corporation (waterfrontoronto.bonfirehub.ca).',
    },
    {
        'slug': 'exhibition-place',
        'body': 'Exhibition Place',
        'portal_url': 'https://explace.bonfirehub.ca',
        'enabled': True,
        'permission': 'docs/permissions/2026-10-22-bonfire.md',
        'attribution': 'Source: Exhibition Place (explace.bonfirehub.ca).',
    },
    {
        'slug': 'the-atmospheric-fund',
        'body': 'The Atmospheric Fund',
        'portal_url': 'https://taf.bonfirehub.ca',
        'enabled': True,
        'permission': 'docs/permissions/2026-10-22-bonfire.md',
        'attribution': 'Source: The Atmospheric Fund (taf.bonfirehub.ca).',
    },
    {
        'slug': 'toronto-transit-commission',
        'body': 'Toronto Transit Commission',
        'portal_url': 'https://ttc.bonfirehub.ca',
        'enabled': True,
        'open_only': True,
        'permission': 'docs/permissions/2026-10-22-bonfire.md',
        'attribution': 'Source: Toronto Transit Commission (ttc.bonfirehub.ca).',
    },
]

MERX_PORTALS = [
    dict(slug='toronto-transit-commission', body='Toronto Transit Commission',
         portal_url='https://www.merx.com/ttc', enabled=True,
         permission='docs/permissions/2026-10-24-merx.md',
         attribution='Source: Toronto Transit Commission, published on MERX.'),
    dict(slug='toronto-police-service', body='Toronto Police Service', portal_url='https://www.merx.com/torontopolice', enabled=True,
         permission='docs/permissions/2026-10-24-merx.md', listing_buyer_label=False,
         attribution='Source: Toronto Police Service, published on MERX.'),
    dict(slug='toronto-parking-authority', body='Toronto Parking Authority', portal_url='https://www.merx.com/torontoparkingauthority', enabled=True,
         permission='docs/permissions/2026-10-24-merx.md', listing_buyer_label=False,
         attribution='Source: Toronto Parking Authority, published on MERX.'),
    dict(slug='createto', body='CreateTO (Build Toronto, TPLC)', portal_url='https://www.merx.com/buildtorontotplc', enabled=True,
         permission='docs/permissions/2026-10-24-merx.md', listing_buyer_label=False,
         attribution='Source: CreateTO (Build Toronto, TPLC), published on MERX.'),
    dict(slug='toronto-pan-am-sports-centre', body='Toronto Pan Am Sports Centre', portal_url='https://www.merx.com/torontopanamsportscentre', enabled=True,
         permission='docs/permissions/2026-10-24-merx.md', listing_buyer_label=False,
         attribution='Source: Toronto Pan Am Sports Centre, published on MERX.'),
]
