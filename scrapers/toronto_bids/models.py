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
    publisher_identity_json: str | None = None
    publisher_award_identity_json: str | None = None
    source: str = ""


@dataclass(frozen=True)
class Award:
    document_number: str
    supplier_name_raw: str | None = None
    award_amount: str | None = None
    award_date: str | None = None
    source: str = ""
    award_amount_numeric: float | None = field(init=False, default=None)

    def __post_init__(self):
        object.__setattr__(self, "award_amount_numeric", parse_amount(self.award_amount))


@dataclass(frozen=True)
class NonCompetitive:
    workspace_number: str
    supplier_name_raw: str | None = None
    reason: str | None = None
    contract_amount: str | None = None
    contract_date: str | None = None
    division: str | None = None
    council_authority_link: str | None = None
    odata_id: str | None = None
    source: str = ""
    recipients: str | None = None  # JSON array of published supplier lines
    contract_amount_numeric: float | None = field(init=False, default=None)

    def __post_init__(self):
        # Derived on the model so every source populates *_numeric consistently.
        object.__setattr__(self, "contract_amount_numeric", parse_amount(self.contract_amount))


@dataclass(frozen=True)
class AribaPosting:
    rfx_id: str
    document_number: str | None = None
    title: str | None = None
    posting_type: str | None = None  # detail 'type' field (unreliable, often "RFI")
    status: str | None = None
    customer_name: str | None = None
    posted_date: str | None = None
    close_date: str | None = None
    categories: str | None = None  # JSON array
    amount_min: str | None = None
    amount_max: str | None = None
    currency: str | None = None
    public_posting_url: str | None = None
    sourcing_url: str | None = None
    external_rfx_id: str | None = None
    raw_json: str | None = None
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
    """Forward-looking capital project (no document_number; keyed on name)."""
    name: str
    contract_number: str | None = None
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
    """Single bid on a solicitation (winning or losing)."""
    bidder_name_raw: str
    # reference is NULL for post-2025-10-01 Award Summary Forms; document_number is NULL pre-2019.
    reference: str | None = None
    document_number: str | None = None
    bid_price: str | None = None
    # 'including' | 'excluding' | None; required to compare prices across rows.
    hst_basis: str | None = None
    price_header: str | None = None
    status: str | None = None
    source: str = ""
    bid_price_numeric: float | None = field(init=False, default=None)

    def __post_init__(self):
        object.__setattr__(self, "bid_price_numeric", parse_bid_price(self.bid_price))


@dataclass(frozen=True)
class BidScope:
    reference: str
    source: str
    scope_key: str
    scope_kind: str
    proof_sha256: str
    document_number: str | None = None
    scope_label: str | None = None
    scope_status: str | None = None


@dataclass(frozen=True)
class BidResponse:
    scope_id: int
    response_key: str
    bidder_name_raw: str
    identity_status: str
    response_kind: str
    row_quotation: str
    bid_id: int | None = None
    alternative_label: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class BidResponsePrice:
    response_id: int
    price_key: str
    value_raw: str
    value_kind: str
    price_header: str
    quotation: str
    unit: str | None = None
    price_basis: str | None = None
    hst_basis: str | None = None
    value_numeric: float | None = field(init=False,default=None)

    def __post_init__(self):
        # A percentage or unit rate is never a currency total.
        if self.value_kind=='money' and self.unit in (None,'total','CAD'):
            object.__setattr__(self,'value_numeric',parse_bid_price(self.value_raw))


@dataclass(frozen=True)
class CouncilItem:
    reference: str
    title: str | None = None
    decision_text: str | None = None


@dataclass(frozen=True)
class BackgroundPdf:
    url: str
    reference: str | None = None         # council item; NULL for kind='award_summary'
    document_number: str | None = None   # set for kind='award_summary'; NULL for council PDFs
    kind: str | None = None
    local_path: str | None = None
    sha256: str | None = None
    text: str | None = None
    award_review_context_json: str | None = None


@dataclass(frozen=True)
class CompositeAward:
    """Award line from a 2009-2012 Bid Committee composite report (keyed on call_number)."""
    call_number: str
    call_number_raw: str | None = None
    reference: str | None = None
    title: str | None = None
    supplier_name_raw: str | None = None
    # Initial-term net-of-taxes figure (excludes option years).
    award_value: str | None = None
    source: str = ""
    award_value_numeric: float | None = field(init=False, default=None)

    def __post_init__(self):
        object.__setattr__(self, "award_value_numeric", parse_amount(self.award_value))


@dataclass(frozen=True)
class AribaAttachment:
    """Index entry for one file inside an Ariba attachment zip bundle on disk."""
    document_number: str
    filename: str
    path: str | None = None
    file_size: int | None = None
    crc32: str | None = None
    zip_name: str | None = None
    zip_sha256: str | None = None


@dataclass(frozen=True)
class Buyer:
    """Procuring agency or corporation outside the City PMMD feed."""
    slug: str
    name: str | None = None
    kind: str | None = None  # 'agency' | 'corporation'
    partnered: int = 0
    funding_share: float | None = None
    platform: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class AgencySolicitation:
    buyer_id: int
    native_ref: str
    title: str | None = None
    status: str | None = None
    posted_date: str | None = None
    closing_date: str | None = None
    portal_url: str | None = None
    source: str = ""


@dataclass(frozen=True)
class AgencyAward:
    buyer_id: int
    native_ref: str
    supplier_name_raw: str | None = None
    award_amount: str | None = None
    value_confidential: int = 0
    award_date: str | None = None
    publication_date: str | None = None
    report_url: str | None = None
    source: str = ""
    award_amount_numeric: float | None = field(init=False, default=None)

    def __post_init__(self):
        object.__setattr__(self, "award_amount_numeric", parse_amount(self.award_amount))


@dataclass(frozen=True)
class AgencyBid:
    buyer_id: int
    native_ref: str
    bidder_name_raw: str
    bid_price: str | None = None
    report_url: str | None = None
    source: str = ""
    hst_basis: str | None = None
    price_header: str | None = None
    status: str | None = None
    result_stage: str | None = None
    bid_price_numeric: float | None = field(init=False, default=None)

    def __post_init__(self):
        object.__setattr__(self, "bid_price_numeric", parse_bid_price(self.bid_price))


@dataclass(frozen=True)
class PortalListing:
    """A buyer's published portal page; a printed reference can be absent or reused."""
    buyer_id: int
    portal_url: str
    native_ref: str | None = None
    title: str | None = None
    status: str | None = None
    posted_date: str | None = None
    closing_date: str | None = None
    source: str = "bids_tenders"
