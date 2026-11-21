import json
import logging
import re
from typing import Iterable, Iterator

from toronto_bids import config
from toronto_bids.linking.document_number import normalize_document_number
from toronto_bids.models import Award, CapitalProject, NonCompetitive, Solicitation
from toronto_bids.sources.base import Row

_LOG = logging.getLogger(__name__)


def resolve_resource_id(http, slug: str) -> str:
    """Resolve the datastore-active resource UUID for a CKAN slug (rotates on refresh)."""
    data = http.get_json(config.CKAN_BASE + "package_show", params={"id": slug})
    if data.get("success") is False:
        raise RuntimeError(f"CKAN package_show failed for '{slug}': {data.get('error', '<no detail>')}")
    resources = data["result"]["resources"]
    for res in resources:
        if res.get("datastore_active"):
            return res["id"]
    raise LookupError(f"No datastore-active resource for CKAN dataset '{slug}'")


def fetch_datastore(http, resource_id: str, page_size: int = 10000) -> Iterator[dict]:
    """Yield every record from a CKAN datastore resource, paging by offset."""
    offset = 0
    while True:
        data = http.get_json(
            config.CKAN_BASE + "datastore_search",
            params={"resource_id": resource_id, "limit": page_size, "offset": offset},
        )
        if data.get("success") is False:
            raise RuntimeError(f"CKAN datastore_search failed for resource '{resource_id}': {data.get('error', '<no detail>')}")
        records = data["result"]["records"]
        if not records:
            return
        yield from records
        offset += len(records)


def _clean(value):
    """Return stripped string, or None for None, empty, or 'None'."""
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text == "None":
        return None
    return text


def decode_award_entries(value: object) -> list[object] | None:
    """Decode the CKAN Award array, returning None for a non-array value."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, list) else None


def normalize_awarded(raw: dict) -> Iterable[Row]:
    doc = normalize_document_number(raw.get("Document Number"))
    if doc is None:
        return
    yield Solicitation(
        document_number=doc,
        status="Awarded",
        rfx_type=_clean(raw.get("RFx (Solicitation) Type")),
        category=_clean(raw.get("High Level Category")),
        description=_clean(raw.get("Solicitation Document Description")),
        division=_clean(raw.get("Division")),
        buyer_name=_clean(raw.get("Buyer Name")),
        buyer_email=_clean(raw.get("Buyer Email")),
        buyer_phone=_clean(raw.get("Buyer Phone Number")),
        source="ckan_awarded",
    )
    value = raw.get("Award")
    entries = decode_award_entries(value)
    if entries is None:
        structured_value = isinstance(value, dict) or (
            isinstance(value, str) and value.lstrip().startswith(("[", "{"))
        )
        if "Successful Supplier" not in raw or structured_value:
            _LOG.warning("CKAN %s: Award must be a JSON array", doc)
            return
        # Older captures used one scalar award per record.
        supplier = _clean(raw.get("Successful Supplier"))
        if supplier is not None:
            yield Award(
                document_number=doc,
                supplier_name_raw=supplier,
                award_amount=_clean(value),
                award_date=_clean(raw.get("Award Authority Obtained Date")),
                source="ckan_awarded",
            )
        return

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            _LOG.warning("CKAN %s: Award[%d] must be an object", doc, index)
            continue
        supplier = _clean(entry.get("Successful_Bidder"))
        if supplier is None:
            _LOG.warning("CKAN %s: Award[%d] has no Successful_Bidder", doc, index)
            continue
        yield Award(
            document_number=doc,
            supplier_name_raw=supplier,
            award_amount=_clean(entry.get("Award_Amount")),
            award_date=_clean(entry.get("Date_Awarded")) or _clean(entry.get("AwardedDate")),
            source="ckan_awarded",
        )


def normalize_open(raw: dict):
    doc = normalize_document_number(raw.get("Document Number"))
    if doc is None:
        return
    yield Solicitation(
        document_number=doc,
        status="Open",
        rfx_type=_clean(raw.get("RFx (Solicitation) Type")),
        noip_type=_clean(raw.get("NOIP (Notice of Intended Procurement) Type")),
        issue_date=_clean(raw.get("Issue Date")),
        submission_deadline=_clean(raw.get("Submission Deadline")),
        category=_clean(raw.get("High Level Category")),
        description=_clean(raw.get("Solicitation Document Description")),
        division=_clean(raw.get("Division")),
        buyer_name=_clean(raw.get("Buyer Name")),
        buyer_email=_clean(raw.get("Buyer Email")),
        buyer_phone=_clean(raw.get("Buyer Phone Number")),
        wards=_clean(raw.get("Wards")),
        source="ckan_open",
    )


def normalize_noncompetitive(raw: dict):
    workspace = _clean(raw.get("Workspace Number"))
    if workspace is None:
        return
    yield NonCompetitive(
        workspace_number=workspace,
        supplier_name_raw=_clean(raw.get("Supplier Name")),
        reason=_clean(raw.get("Reason")),
        contract_amount=_clean(raw.get("Contract Amount")),
        contract_date=_clean(raw.get("Contract Date")),
        division=_clean(raw.get("Division")),
        source="ckan_noncomp",
    )


# Construction contract number embedded in capital project names (e.g. '25ECS-MI-02SW').
_CONTRACT_NO = re.compile(r"\b(\d{2}[A-Z]{2,4}(?:-[A-Z0-9]+){1,3})\b")


def normalize_capital_project(raw: dict):
    name = _clean(raw.get("Name and Construction Contract Number"))
    if name is None:
        return
    match = _CONTRACT_NO.search(name)
    yield CapitalProject(
        name=name,
        contract_number=match.group(1) if match else None,
        type_of_work=_clean(raw.get("Type of Work")),
        scope=_clean(raw.get("Scope of Work: Detailed Description")),
        delivery_division=_clean(raw.get("Delivery Division")),
        owner_division=_clean(raw.get("Project Owner (Division)")),
        target_sourcing_year=_clean(raw.get("Target Sourcing Year")),
        target_award_year=_clean(raw.get("Target Award Year")),
        sourcing_type=_clean(raw.get("Sourcing Type")),
        estimated_range=_clean(raw.get("Estimated Range")),
        estimated_term_months=_clean(raw.get("Estimated Contract Term (Months)")),
        source="ckan_pipeline",
    )


_NORMALIZERS = {
    "awarded": normalize_awarded,
    "open": normalize_open,
    "noncompetitive": normalize_noncompetitive,
    "capital_pipeline": normalize_capital_project,
}


class CkanSource:
    """CKAN dataset adapter."""

    def __init__(self, name: str, slug: str, kind: str, overwrite: bool = False):
        if kind not in _NORMALIZERS:
            raise ValueError(f"Unknown CKAN kind: {kind}")
        self.name = name
        self.slug = slug
        self.kind = kind
        # False by default because OData is authoritative; True for capital_pipeline.
        self.overwrite = overwrite

    def fetch(self, http) -> Iterable[dict]:
        resource_id = resolve_resource_id(http, self.slug)
        yield from fetch_datastore(http, resource_id)

    def normalize(self, raw: dict) -> Iterable[Row]:
        yield from _NORMALIZERS[self.kind](raw)
