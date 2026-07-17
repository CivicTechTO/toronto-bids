from dataclasses import dataclass, field

from toronto_bids.amount import parse_amount, parse_bid_price


@dataclass(frozen=True)
class Solicitation:
    document_number: str
    status: str | None = None
    rfx_type: str | None = None
    noip_type: str | None = None
    form_type: str | None = None
    title: str | None = None
    description: str | None = None
    issue_date: str | None = None
    submission_deadline: str | None = None
    category: str | None = None
    division: str | None = None
    buyer_name: str | None = None
    buyer_email: str | None = None
    buyer_phone: str | None = None
    wards: str | None = None
    ariba_posting_link: str | None = None
    odata_id: str | None = None
    source: str = ""


@dataclass(frozen=True)
class Award:
    document_number: str
    supplier_name_raw: str | None = None
    award_amount: str | None = None          # the City's string, verbatim — never summable
    award_date: str | None = None
    source: str = ""
    # Derived, not passed: see NonCompetitive.__post_init__ for why it lives here.
    award_amount_numeric: float | None = field(init=False, default=None)

    def __post_init__(self):
        object.__setattr__(self, "award_amount_numeric", parse_amount(self.award_amount))


@dataclass(frozen=True)
class NonCompetitive:
    workspace_number: str
    supplier_name_raw: str | None = None
    reason: str | None = None
    contract_amount: str | None = None       # the City's string, verbatim — never summable
    contract_date: str | None = None
    division: str | None = None
    council_authority_link: str | None = None
    odata_id: str | None = None
    source: str = ""
    contract_amount_numeric: float | None = field(init=False, default=None)

    def __post_init__(self):
        # Derived on the model rather than at each normalizer's call site (odata + ckan, two
        # each): every source that sets the raw string gets the number for free, and a new
        # one cannot forget it and silently NULL the column — the same failure mode
        # sources/schema_check.py exists to catch.
        object.__setattr__(self, "contract_amount_numeric", parse_amount(self.contract_amount))


@dataclass(frozen=True)
class AribaPosting:
    rfx_id: str
    document_number: str | None = None
    title: str | None = None
    posting_type: str | None = None      # detail 'type' field — unreliable (often "RFI")
    status: str | None = None
    customer_name: str | None = None
    posted_date: str | None = None
    close_date: str | None = None
    categories: str | None = None        # JSON array of category names
    amount_min: str | None = None
    amount_max: str | None = None
    currency: str | None = None
    public_posting_url: str | None = None
    sourcing_url: str | None = None      # authenticated event URL (for a later attachments phase)
    external_rfx_id: str | None = None   # raw e.g. "Doc5672751291"
    raw_json: str | None = None          # detail JSON snapshot, or None if the detail call failed
    source: str = ""


@dataclass(frozen=True)
class SuspendedFirm:
    supplier_name_raw: str
    status: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    suspension_type: str | None = None
    council_authority: str | None = None
    source: str = ""


@dataclass(frozen=True)
class Supplier:
    supplier_key: str
    display_name: str | None = None
    variants: str | None = None


@dataclass(frozen=True)
class CapitalProject:
    """A solicitation the City intends to issue but has not yet (#69).

    Forward-looking, so it has no document_number and never joins the spine — a project
    only gets one when it is actually solicited. Keyed on the City's combined name+contract
    string because 'No.' is a row index that churns on every refresh.
    """
    name: str
    contract_number: str | None = None    # e.g. '25ECS-MI-02SW', teased out of `name`
    type_of_work: str | None = None
    scope: str | None = None
    delivery_division: str | None = None
    owner_division: str | None = None
    target_sourcing_year: str | None = None
    target_award_year: str | None = None
    sourcing_type: str | None = None
    estimated_range: str | None = None
    estimated_term_months: str | None = None
    source: str = ""


@dataclass(frozen=True)
class Bid:
    """One bid on one solicitation — including the ones that lost (#84).

    Rewrite spec §2.5.2 called these "never published anywhere. **Unrecoverable.**" They are
    on every Bid Award Panel agenda. This is what makes the archive answer whether a
    procurement was actually competitive, not merely what it cost.
    """
    bidder_name_raw: str
    # Both identifiers are partial, and which one is present says where the bid came from.
    # A Bid Award Panel bid always has a council item and had no document number before 2019
    # (Toronto had no Ariba). An Award Summary Form bid (#114) is the reverse: it is keyed on
    # the document number and has no council item at all — the panel that produced council
    # items was abolished on 2025-10-01. So neither can be required, and `bid_key` COALESCEs
    # both.
    reference: str | None = None         # council item, e.g. '2022.BA189.2'
    document_number: str | None = None   # NULL pre-2019: Toronto had no Ariba doc numbers yet
    bid_price: str | None = None         # verbatim, footnote marker and all
    # 'including' | 'excluding' | None. NOT decoration: 5,752 bids are quoted including HST
    # and 4,083 excluding it, so a bare price is two incomparable things in one column.
    hst_basis: str | None = None
    price_header: str | None = None      # the column header verbatim — provenance for hst_basis
    source: str = ""
    bid_price_numeric: float | None = field(init=False, default=None)

    def __post_init__(self):
        object.__setattr__(self, "bid_price_numeric", parse_bid_price(self.bid_price))


@dataclass(frozen=True)
class CouncilItem:
    reference: str
    title: str | None = None
    decision_text: str | None = None


@dataclass(frozen=True)
class BackgroundPdf:
    url: str
    reference: str | None = None         # council item; NULL for kind='award_summary' (#114)
    document_number: str | None = None   # set for kind='award_summary'; NULL for council PDFs
    kind: str | None = None
    local_path: str | None = None
    sha256: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class CompositeAward:
    """One award line from a 2009-2012 Bid Committee composite report's appendices (#96).

    A third keyspace. These predate Ariba, so they carry a Call Number and no
    document_number, and nothing joins them to `solicitation` — see the composite_award
    comment in schema.sql. For 2009-2011 this is the only record the archive has: the City's
    feed publishes 0, 1 and 12 awards for those years against the 799 sitting in these
    reports.
    """
    call_number: str                     # normalized, e.g. '3905-10-0097' / '317-2010'
    call_number_raw: str | None = None   # as council wrote it, prefix vocabulary and all
    reference: str | None = None         # the council item that carried it, e.g. '2011.BD5.1'
    title: str | None = None
    supplier_name_raw: str | None = None
    # Verbatim, as `award.award_amount` is. The numeric is the FIRST net-of-taxes figure —
    # the initial term, excluding option years. Confirmed against the feed on 137 of 139
    # appendices it also published.
    award_value: str | None = None
    source: str = ""
    award_value_numeric: float | None = field(init=False, default=None)

    def __post_init__(self):
        object.__setattr__(self, "award_value_numeric", parse_amount(self.award_value))


@dataclass(frozen=True)
class AribaAttachment:
    """One file inside the document bundle behind a solicitation's Ariba "Respond" gate (#117).

    document_number is the Ariba event and joins solicitation.document_number. The bytes live
    on disk under <DATA_DIR>/ariba/attachments/; this row is the index. file_size and crc32
    come from the zip central directory (no decompression); zip_sha256 fingerprints the whole
    bundle.
    """
    document_number: str
    filename: str
    path: str | None = None
    file_size: int | None = None
    crc32: str | None = None
    zip_name: str | None = None
    zip_sha256: str | None = None
