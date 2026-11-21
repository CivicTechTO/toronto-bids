"""Verify that City OData and CKAN feeds still expose all fields read by normalizers.

Runs as a normal source so drift is logged to sync_run without blocking ingestion.
"""
from itertools import islice

from toronto_bids import config
from toronto_bids.sources.ckan import decode_award_entries, fetch_datastore, resolve_resource_id
from toronto_bids.sources.odata import fetch_entityset

# entityset -> (top-level fields, nested Awarded_Suppliers fields; AwardedDate is solicitation-only).
_ODATA = {
    "odata_solicitations": (config.ODATA_SOLICITATIONS, {
        "Solicitation_Document_Number", "Status", "Solicitation_Document_Type",
        "Solicitation_Form_Type", "Posting_Title", "Solicitation_Document_Description",
        "Issue_Date", "Closing_Date", "High_Level_Category", "Client_Division",
        "Buyer_Name", "Buyer_Email", "Buyer_Phone_Number", "Wards",
        "Ariba_Discovery_Posting_Link", "id", "Awarded_Suppliers", "Solicitation_Award_Details",
    }, {"Successful_Bidder", "Award_Amount", "Date_Awarded", "AwardedDate"}),
    "odata_noncompetitive": (config.ODATA_NONCOMPETITIVE, {
        "Non_Competitive_Reference_Number", "Non_Competitive_Reason", "Client_Division",
        "Council_Authority_Link_to_Staff_Report", "id", "Awarded_Suppliers",
    }, {"Successful_Bidder", "Award_Amount", "Date_Awarded"}),
}

_CKAN = {
    "ckan_awarded": (config.CKAN_AWARDED_SLUG, {
        "Document Number", "RFx (Solicitation) Type", "High Level Category",
        "Solicitation Document Description", "Division", "Buyer Name", "Buyer Email",
        "Buyer Phone Number", "Award",
    }),
    "ckan_open": (config.CKAN_OPEN_SLUG, {
        "Document Number", "RFx (Solicitation) Type",
        "NOIP (Notice of Intended Procurement) Type", "Issue Date", "Submission Deadline",
        "High Level Category", "Solicitation Document Description", "Division",
        "Buyer Name", "Buyer Email", "Buyer Phone Number", "Wards",
    }),
    "ckan_noncomp": (config.CKAN_NONCOMP_SLUG, {
        "Workspace Number", "Supplier Name", "Reason", "Contract Amount",
        "Contract Date", "Division",
    }),
    "ckan_pipeline": (config.CKAN_PIPELINE_SLUG, {
        "Name and Construction Contract Number", "Type of Work",
        "Scope of Work: Detailed Description", "Delivery Division",
        "Project Owner (Division)", "Target Sourcing Year", "Target Award Year",
        "Sourcing Type", "Estimated Range", "Estimated Contract Term (Months)",
    }),
}
_CKAN_AWARD_ENTRY = {"Successful_Bidder", "Award_Amount", "Date_Awarded", "AwardedDate"}


# Sample enough records to find at least one nested award entry.
_SAMPLE = 50


def missing_fields(feed: str, record: dict | None, expected: set) -> list[str]:
    """Return drift error strings for missing expected keys in record."""
    if record is None:
        return [f"{feed}: feed returned no records at all"]
    gone = sorted(expected - record.keys())
    if not gone:
        return []
    return [f"{feed}: missing {gone} — renamed or dropped; fix the normalizer and the "
            f"declared fields in sources/schema_check.py together"]


def check_odata(http, feed: str, entityset: str, expected: set, supplier_expected: set) -> list[str]:
    records = list(islice(fetch_entityset(http, entityset, page_size=_SAMPLE), _SAMPLE))
    problems = missing_fields(feed, records[0] if records else None, expected)
    if problems:
        return problems
    # Find a record with non-empty Awarded_Suppliers to check nested fields.
    supplier = next((s for r in records for s in (r.get("Awarded_Suppliers") or [])), None)
    if supplier is None:
        return [f"{feed}: no awarded supplier in the first {_SAMPLE} records, so "
                f"{sorted(supplier_expected)} went unchecked"]
    return missing_fields(f"{feed}.Awarded_Suppliers", supplier, supplier_expected)


def check_ckan(http, feed: str, slug: str, expected: set) -> list[str]:
    resource_id = resolve_resource_id(http, slug)
    if feed == "ckan_awarded":
        records = list(islice(fetch_datastore(http, resource_id, page_size=_SAMPLE), _SAMPLE))
        return check_ckan_awards(feed, records, expected)
    record = next(iter(fetch_datastore(http, resource_id, page_size=1)), None)
    return missing_fields(feed, record, expected)


def check_ckan_awards(feed: str, records: list[dict], expected: set) -> list[str]:
    if not records:
        return missing_fields(feed, None, expected)
    checked_award = False
    for record_number, record in enumerate(records, start=1):
        problems = missing_fields(f"{feed} (sample record {record_number})", record, expected)
        if problems:
            return problems
        entries = decode_award_entries(record["Award"])
        if entries is None:
            return [f"{feed}.Award (sample record {record_number}): expected a JSON array"]
        for index, entry in enumerate(entries):
            location = f"{feed}.Award[{index}] (sample record {record_number})"
            if not isinstance(entry, dict):
                return [f"{location}: expected an object"]
            problems = missing_fields(location, entry, _CKAN_AWARD_ENTRY)
            if problems:
                return problems
            checked_award = True
    if not checked_award:
        return [f"{feed}.Award: no award entries in the first {len(records)} records, so "
                f"{sorted(_CKAN_AWARD_ENTRY)} went unchecked"]
    return []


class SchemaCheckSource:
    """Check feed schemas and raise if any expected field is missing."""

    name = "schema_check"
    overwrite = True

    def fetch(self, http):
        problems = []
        for feed, (entityset, expected, supplier_expected) in _ODATA.items():
            problems += check_odata(http, feed, entityset, expected, supplier_expected)
        for feed, (slug, expected) in _CKAN.items():
            problems += check_ckan(http, feed, slug, expected)
        if problems:
            raise ValueError("City feed check failed: " + "; ".join(problems))
        return []

    def normalize(self, raw):
        return []
