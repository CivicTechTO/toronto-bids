"""The buyer dimension seed (#135). Hardcoded like pipeline.default_sources()."""
from toronto_bids.models import Buyer
from toronto_bids.store import db

DEFAULT_BUYERS = [
    Buyer(slug="toronto-community-housing",
          name="Toronto Community Housing and Toronto Seniors Housing", kind="corporation",
          partnered=0, funding_share=None, platform="Bonfire",
          notes="Shared public TCHC/TSHC portal (#107). Listings include TCHC, TSHC and "
                "joint departments; this profile groups the portal and does not assign "
                "each posting to one corporation. Closed/Awarded listing badges do not "
                "establish supplier or value award facts."),
    Buyer(slug="toronto-public-library", name="Toronto Public Library", kind="agency",
          partnered=0, funding_share=None, platform="Bonfire",
          notes="Public Open/Past opportunity listings captured from the Library's "
                "linked Bonfire portal (#110); closed listings do not establish awards."),
    Buyer(slug="waterfront-toronto", name="Waterfront Toronto", kind="corporation",
          partnered=1, funding_share=None, platform="own-site / Bonfire",
          notes="Joint federal, Ontario and Toronto corporation. Own-site monthly award "
                "publication captured under explicit permission (#108); Bonfire is separate (#134)."),
    Buyer(slug="toronto-zoo", name="Toronto Zoo", kind="agency", partnered=0,
          funding_share=None, platform="bids&tenders",
          notes="Board of Management on TMMIS as the ZB committee; portal "
                "torontozoo.bidsandtenders.ca (gated, #135)."),
    Buyer(slug="trca", name="Toronto and Region Conservation Authority", kind="agency",
          partnered=1, funding_share=0.626, platform="bids&tenders",
          notes="Partnered: six municipalities fund it; Toronto pays 62.6% of the 2025 "
                "operating levy. Bill 97 amalgamates TRCA away 2027-02-01. Venue history "
                "is mixed (Biddingo through ~2023, then trca.bidsandtenders.ca)."),
    Buyer(slug="exhibition-place", name="Exhibition Place", kind="agency", partnered=0,
          funding_share=None, platform="Bonfire",
          notes="City agency (Board of Governors); left the PMMD feed in 2019 for its own "
                "Bonfire portal. Awards captured from Board of Governors reports on legdocs "
                "(TMMIS EP committee); the Bonfire portal is gated (#134)."),
]


def seed_buyers(conn) -> dict[str, int]:
    """Upsert the hardcoded buyers; return {slug: buyer_id}. Idempotent."""
    for buyer in DEFAULT_BUYERS:
        db.upsert_row(conn, buyer, overwrite=True)
    conn.commit()
    return {r["slug"]: r["id"] for r in conn.execute("SELECT slug, id FROM buyer")}
