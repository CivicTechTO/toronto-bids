from typing import Iterable, Iterator

from toronto_bids import config
from toronto_bids.linking.document_number import normalize_document_number
from toronto_bids.models import Award, NonCompetitive, Solicitation
from toronto_bids.sources.base import Row


def resolve_resource_id(http, slug: str) -> str:
    """Resolve the datastore-active resource UUID for a CKAN dataset slug.

    Resource UUIDs rotate on refresh, so this must be called at runtime.
    """
    data = http.get_json(config.CKAN_BASE + "package_show", params={"id": slug})
    if data.get("success") is False:
        raise RuntimeError(f"CKAN package_show failed for '{slug}': {data.get('error')}")
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
            raise RuntimeError(f"CKAN datastore_search failed for resource '{resource_id}': {data.get('error')}")
        records = data["result"]["records"]
        if not records:
            return
        yield from records
        offset += len(records)


def _clean(value):
    """Normalize CKAN empties to None. Treats '', 'None', and null as missing."""
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text == "None":
        return None
    return text


def normalize_awarded(raw: dict):
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
    supplier = _clean(raw.get("Successful Supplier"))
    if supplier is not None:
        yield Award(
            document_number=doc,
            supplier_name_raw=supplier,
            award_amount=_clean(raw.get("Award")),
            award_date=_clean(raw.get("Award Authority Obtained Date")),
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


_NORMALIZERS = {
    "awarded": normalize_awarded,
    "open": normalize_open,
    "noncompetitive": normalize_noncompetitive,
}


class CkanSource:
    """A CKAN dataset adapter."""

    overwrite = False  # CKAN backfills; OData is the spine.

    def __init__(self, name: str, slug: str, kind: str):
        if kind not in _NORMALIZERS:
            raise ValueError(f"Unknown CKAN kind: {kind}")
        self.name = name
        self.slug = slug
        self.kind = kind

    def fetch(self, http) -> Iterable[dict]:
        resource_id = resolve_resource_id(http, self.slug)
        yield from fetch_datastore(http, resource_id)

    def normalize(self, raw: dict) -> Iterable[Row]:
        yield from _NORMALIZERS[self.kind](raw)
