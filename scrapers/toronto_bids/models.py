from dataclasses import dataclass


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
    award_amount: str | None = None
    award_date: str | None = None
    source: str = ""


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
