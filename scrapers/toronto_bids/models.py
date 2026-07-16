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
