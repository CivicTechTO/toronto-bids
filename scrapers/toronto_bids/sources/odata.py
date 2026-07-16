from typing import Iterable, Iterator

from toronto_bids import config
from toronto_bids.linking.document_number import normalize_document_number
from toronto_bids.models import Award, NonCompetitive, Solicitation
from toronto_bids.sources.base import Row
from toronto_bids.title import clean_title

# odata.metadata=none returns {"@odata.count": N, "value": [...]}, records in "value".
_FORMAT = "application/json;odata.metadata=none"


def _clean(value):
    if value is None:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def fetch_entityset(http, entityset: str, page_size: int = 1000) -> Iterator[dict]:
    """Yield every record from an OData entity set, paging with $skip/$top."""
    skip = 0
    while True:
        data = http.get_json(
            config.ODATA_BASE + entityset,
            params={"$format": _FORMAT, "$top": page_size, "$skip": skip},
        )
        records = data.get("value", [])
        if not records:
            return
        yield from records
        skip += len(records)


def normalize_solicitation(raw: dict) -> Iterable[Row]:
    doc = normalize_document_number(raw.get("Solicitation_Document_Number"))
    if doc is None:
        return
    yield Solicitation(
        document_number=doc,
        status=_clean(raw.get("Status")),
        rfx_type=_clean(raw.get("Solicitation_Document_Type")),
        noip_type=None,
        form_type=_clean(raw.get("Solicitation_Form_Type")),
        # A placeholder title is spelled NULL so COALESCE stops it winning (#70).
        title=clean_title(_clean(raw.get("Posting_Title"))),
        description=_clean(raw.get("Solicitation_Document_Description")),
        issue_date=_clean(raw.get("Issue_Date")),
        submission_deadline=_clean(raw.get("Closing_Date")),
        category=_clean(raw.get("High_Level_Category")),
        division=_clean(raw.get("Client_Division")),
        buyer_name=_clean(raw.get("Buyer_Name")),
        buyer_email=_clean(raw.get("Buyer_Email")),
        buyer_phone=_clean(raw.get("Buyer_Phone_Number")),
        wards=_clean(raw.get("Wards")),
        ariba_posting_link=_clean(raw.get("Ariba_Discovery_Posting_Link")),
        odata_id=_clean(raw.get("id")),
        source="odata",
    )
    for supplier in raw.get("Awarded_Suppliers") or []:
        name = _clean(supplier.get("Successful_Bidder"))
        if name is None:
            continue
        yield Award(
            document_number=doc,
            supplier_name_raw=name,
            award_amount=_clean(supplier.get("Award_Amount")),
            award_date=_clean(supplier.get("Date_Awarded")) or _clean(supplier.get("AwardedDate")),
            source="odata",
        )


def normalize_noncompetitive(raw: dict) -> Iterable[Row]:
    workspace = _clean(raw.get("Non_Competitive_Reference_Number"))
    if workspace is None:
        return
    suppliers = raw.get("Awarded_Suppliers") or []
    first = suppliers[0] if suppliers else {}
    yield NonCompetitive(
        workspace_number=workspace,
        supplier_name_raw=_clean(first.get("Successful_Bidder")),
        reason=_clean(raw.get("Non_Competitive_Reason")),
        contract_amount=_clean(first.get("Award_Amount")),
        contract_date=_clean(first.get("Date_Awarded")),
        division=_clean(raw.get("Client_Division")),
        council_authority_link=_clean(raw.get("Council_Authority_Link_to_Staff_Report")),
        odata_id=_clean(raw.get("id")),
        source="odata",
    )


class ODataSolicitationSource:
    name = "odata_solicitations"
    overwrite = True

    def fetch(self, http) -> Iterable[dict]:
        yield from fetch_entityset(http, config.ODATA_SOLICITATIONS)

    def normalize(self, raw: dict) -> Iterable[Row]:
        yield from normalize_solicitation(raw)


class ODataNonCompetitiveSource:
    name = "odata_noncompetitive"
    overwrite = True

    def fetch(self, http) -> Iterable[dict]:
        yield from fetch_entityset(http, config.ODATA_NONCOMPETITIVE)

    def normalize(self, raw: dict) -> Iterable[Row]:
        yield from normalize_noncompetitive(raw)
