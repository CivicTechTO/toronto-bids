import os
from pathlib import Path

# Load scrapers/.env if python-dotenv is installed (a base dependency). Guarded so a bare
# install without it still works; credentials then just come from the real environment. Keyed to the package's own parent so it works regardless of the caller's cwd.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# Data directory: scrapers/files/ by default, overridable for tests / deployment.
DATA_DIR = Path(
    os.environ.get("TB_DATA_DIR", Path(__file__).resolve().parent.parent / "files")
)
DB_PATH = DATA_DIR / "bids.sqlite"

CKAN_BASE = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/"
ODATA_BASE = (
    "https://secure.toronto.ca/c3api_data/v2/DataAccess.svc/pmmd_solicitations/"
)

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

# Anonymous SAP Business Network Discovery APIs used by the public Explore Leads UI (#235).
ARIBA_SEARCH_URL = (
    "https://portal.us.bn.cloud.ariba.com/tpx/ingress/tps/discovery-core/public/rfx/v1/search"
)
ARIBA_DETAIL_URL = (
    "https://portal.us.bn.cloud.ariba.com/tpx/ingress/tps/discovery-core/odata/public/"
    "discovery/v1/rfx/RfxEvent('{rfx_id}')"
)
# City's public buyer identity, verified against RfxEvent.anid and orgUnitName.
ARIBA_ORG_ANID = "AN01050912625"
ARIBA_SEARCH_PAGE_SIZE = 100
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

# TMMIS agenda-item pages for committee awards after the Bid Award Panel's abolition
# (#164) — kept apart from COUNCIL_AGENDAS_DIR, which holds the (retired) BA/BD series.
COMMITTEE_ITEMS_DIR = DATA_DIR / "council" / "committee_items"

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
# scrapers/.env ships in git with placeholder values (so the repo can stay public) — on a
# machine with no OTHER .env, load_dotenv() above sets these placeholders into the real
# environment, and they read back as non-empty strings. `capture_attachments`'s own "creds
# unset" guard checks truthiness, which a placeholder satisfies, so it never fired: login()
# proceeded and died 30s later inside Playwright with no hint why the fill failed (#184). A
# placeholder reads as unset here instead — the one place both `capture_attachments` and any
# future caller check.
_ARIBA_PLACEHOLDER = {
    "ARIBA_USERNAME": "your-ariba-supplier-username",
    "ARIBA_PASSWORD": "your-ariba-supplier-password",
}


def _real_env(name: str) -> str | None:
    value = os.environ.get(name)
    return None if value == _ARIBA_PLACEHOLDER.get(name) else value


ARIBA_USERNAME = _real_env("ARIBA_USERNAME")
ARIBA_PASSWORD = _real_env("ARIBA_PASSWORD")

# TRCA meeting records (#135/#224): current record on eSCRIBE, historical minute books
# on TRCA's Laserfiche. Both are TRCA's own hosting (open-data licence) — NOT the
# bids&tenders portal, which stays gated.
TRCA_ESCRIBE_BASE = "https://pub-trca.escribemeetings.com/"
TRCA_REPORTS_DIR = DATA_DIR / "agencies" / "trca"
TRCA_HISTORY_DIR = DATA_DIR / "agencies" / "trca_history"
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
#
# `attribution` is the statement the grant's conditions require in the published archive
# (#228); `export/document.py` lifts every enabled portal's into the export's
# `meta.attribution`, so a future grant that asks for attribution adds it HERE, on its own
# entry, and nowhere else. Neither grant prescribes wording — both ask only that the body be
# credited as the data source — so the statement just names the source.
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

# Nonempty bids&tenders replies are recorded automatically before parsing (#226), one
# content-addressed snapshot per record version. --record also logs explicit empty captures.
PORTAL_RECORDINGS_DIR = DATA_DIR / "agencies" / "portal_recordings"

# Exhibition Place Board of Governors (#130): the EP committee on TMMIS, same infrastructure
# as the Zoo's ZB series — headed-browser discovery, plain-HTTP legdocs report PDFs.
EP_AGENDAS_DIR = DATA_DIR / "agencies" / "ep" / "agendas"
EP_REPORTS_DIR = DATA_DIR / "agencies" / "ep"

# Machine-classification labels for the extraction gate (#209).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLASSIFICATION_LABELS_PATH = (
    _REPO_ROOT
    / "docs"
    / "machine-labels"
    / "document-classification"
    / "labels-machine.json"
)

# Anonymous listing metadata only. The maintainer explicitly confirmed Bonfire
# permission with respectful access/backoff; see the referenced record (#110/#134).
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
]
