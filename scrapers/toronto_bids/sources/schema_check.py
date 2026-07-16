"""The schema the City's feeds must provide, and a check that they still do (#10).

Every normalizer reads its fields by name (`raw.get("Posting_Title")`). If the City
renames or drops one, `.get` returns None, that column NULLs out on every row written from
then on, and nothing anywhere complains. This module is the declared schema for the two
machine-readable sources and the check that catches that.

It runs as an ordinary source, which buys two things for free:

  * per-source isolation — drift is reported WITHOUT stopping ingestion, so a renamed
    field never costs us the archive of postings that vanish when they close;
  * visibility — the failure lands in sync_run, on stderr, and in tb sync's exit code (#18).

That trade is deliberate, and the upsert semantics are why it's the right one: every write
COALESCEs, so a NULL never overwrites a value already in the store (see db._upsert_keyed).
Undetected drift therefore costs new rows a column until someone fixes the normalizer, and
the next sync backfills them — recoverable. Aborting the source instead would cost us the
postings themselves, which is not.

Only fields a normalizer actually reads are listed. The feeds carry ~63 fields; the day we
start reading one, it belongs here. Verified live 2026-07-16: all five feeds return one
stable key-set across 500 records each, so a missing key means real drift, not an empty cell.
"""
from itertools import islice

from toronto_bids import config
from toronto_bids.sources.ckan import fetch_datastore, resolve_resource_id
from toronto_bids.sources.odata import fetch_entityset

# entityset -> (top-level fields, fields of each nested Awarded_Suppliers dict).
# AwardedDate is solicitation-only; that asymmetry is why normalize_solicitation falls
# back Date_Awarded -> AwardedDate and normalize_noncompetitive does not.
_ODATA = {
    "odata_solicitations": (config.ODATA_SOLICITATIONS, {
        "Solicitation_Document_Number", "Status", "Solicitation_Document_Type",
        "Solicitation_Form_Type", "Posting_Title", "Solicitation_Document_Description",
        "Issue_Date", "Closing_Date", "High_Level_Category", "Client_Division",
        "Buyer_Name", "Buyer_Email", "Buyer_Phone_Number", "Wards",
        "Ariba_Discovery_Posting_Link", "id", "Awarded_Suppliers",
    }, {"Successful_Bidder", "Award_Amount", "Date_Awarded", "AwardedDate"}),
    "odata_noncompetitive": (config.ODATA_NONCOMPETITIVE, {
        "Non_Competitive_Reference_Number", "Non_Competitive_Reason", "Client_Division",
        "Council_Authority_Link_to_Staff_Report", "id", "Awarded_Suppliers",
    }, {"Successful_Bidder", "Award_Amount", "Date_Awarded"}),
}

# CKAN dataset slug -> the datastore columns the normalizer reads.
_CKAN = {
    "ckan_awarded": (config.CKAN_AWARDED_SLUG, {
        "Document Number", "RFx (Solicitation) Type", "High Level Category",
        "Solicitation Document Description", "Division", "Buyer Name", "Buyer Email",
        "Buyer Phone Number", "Successful Supplier", "Award",
        "Award Authority Obtained Date",
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


# Records to sample per OData feed. One is not enough: the feed carries not-yet-awarded
# solicitations whose Awarded_Suppliers is empty, and record #1 is one of them today, so
# sampling a single record would leave the nested fields silently unchecked. 15 of the
# first 50 carry a supplier (measured 2026-07-16).
_SAMPLE = 50


def missing_fields(feed: str, record: dict | None, expected: set) -> list[str]:
    """Drift complaints for one sampled record. Empty list == the feed still matches."""
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
    # Awarded_Suppliers is a nested list whose fields drift independently of the record
    # around it, and it's empty for anything not yet awarded. Scan the sample for a record
    # that actually has one; finding none means we could not check, which is worth saying
    # out loud rather than passing green.
    supplier = next((s for r in records for s in (r.get("Awarded_Suppliers") or [])), None)
    if supplier is None:
        return [f"{feed}: no awarded supplier in the first {_SAMPLE} records, so "
                f"{sorted(supplier_expected)} went unchecked"]
    return missing_fields(f"{feed}.Awarded_Suppliers", supplier, supplier_expected)


def check_ckan(http, feed: str, slug: str, expected: set) -> list[str]:
    resource_id = resolve_resource_id(http, slug)
    record = next(iter(fetch_datastore(http, resource_id, page_size=1)), None)
    return missing_fields(feed, record, expected)


class SchemaCheckSource:
    """Samples one record per feed and fails the run if a field we read has gone missing."""

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
        return []  # a check, not a data source: it contributes no rows

    def normalize(self, raw):
        return []
