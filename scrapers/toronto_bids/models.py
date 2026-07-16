from dataclasses import dataclass, field

from toronto_bids.amount import parse_amount


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
class CouncilItem:
    reference: str
    title: str | None = None
    decision_text: str | None = None


@dataclass(frozen=True)
class BackgroundPdf:
    url: str
    reference: str | None = None
    kind: str | None = None
    local_path: str | None = None
    sha256: str | None = None
    text: str | None = None
