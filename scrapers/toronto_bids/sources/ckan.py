import re
from typing import Iterable, Iterator

from toronto_bids import config
from toronto_bids.linking.document_number import normalize_document_number
from toronto_bids.models import Award, CapitalProject, NonCompetitive, Solicitation
from toronto_bids.sources.base import Row


def resolve_resource_id(http, slug: str) -> str:
    """Resolve the datastore-active resource UUID for a CKAN dataset slug.

    Resource UUIDs rotate on refresh, so this must be called at runtime.
    """
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


# The City packs a construction contract number into the project name:
#   'Dufferin Transfer Station - Compactors Replacement  - 25ECS-MI-02SW'
# Same shape as the contract numbers seen on Bid Award Panel agendas ('22TR-OM-104-SC-TM')
# and in the legacy archive's folder names ('22ECS-TI-22SP'). Nothing joins on it today —
# these projects have no document number yet — but teasing it out costs one regex and is the
# only identifier a future join could use.
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
    """A CKAN dataset adapter."""

    def __init__(self, name: str, slug: str, kind: str, overwrite: bool = False):
        if kind not in _NORMALIZERS:
            raise ValueError(f"Unknown CKAN kind: {kind}")
        self.name = name
        self.slug = slug
        self.kind = kind
        # False by default: CKAN backfills, OData is the spine. capital_pipeline is the
        # exception — no spine covers it, so CKAN *is* authoritative there, and a project
        # whose target year or scope shifts must land rather than be COALESCEd away.
        self.overwrite = overwrite

    def fetch(self, http) -> Iterable[dict]:
        resource_id = resolve_resource_id(http, self.slug)
        yield from fetch_datastore(http, resource_id)

    def normalize(self, raw: dict) -> Iterable[Row]:
        yield from _NORMALIZERS[self.kind](raw)
