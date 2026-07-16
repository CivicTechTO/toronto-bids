"""Drift detection for the City's feeds (#10).

The failure being caught: a renamed City field makes every normalizer's .get() return
None, so the column NULLs out across all rows and the run still reports success.
"""
import pytest

from toronto_bids import config, pipeline
from toronto_bids.sources.schema_check import SchemaCheckSource, missing_fields

# One record per feed, shaped like the real thing (verified live 2026-07-16). Only the
# fields the normalizers read plus a little noise — the real feeds carry ~63.
ODATA_SOLICITATION = {
    "Solicitation_Document_Number": "3303123110", "Status": "Awarded",
    "Solicitation_Document_Type": "RFQ", "Solicitation_Form_Type": "Goods",
    "Posting_Title": "Widgets", "Solicitation_Document_Description": "Some widgets",
    "Issue_Date": "2025-01-01", "Closing_Date": "2025-02-01",
    "High_Level_Category": "Goods", "Client_Division": "Fleet Services",
    "Buyer_Name": "A Buyer", "Buyer_Email": "b@toronto.ca", "Buyer_Phone_Number": "416",
    "Wards": "All", "Ariba_Discovery_Posting_Link": "https://example.test", "id": "1",
    "Awarded_Suppliers": [{
        "Successful_Bidder": "Acme", "Award_Amount": "100", "Date_Awarded": "2025-02-02",
        "AwardedDate": "2025-02-02", "street": "1 Main St",
    }],
    "Staff_Notes": "unused field the feed happens to carry",
}
ODATA_NONCOMP = {
    "Non_Competitive_Reference_Number": "WS-1", "Non_Competitive_Reason": "Sole source",
    "Client_Division": "Fleet", "Council_Authority_Link_to_Staff_Report": "2025.GG26.3",
    "id": "2",
    "Awarded_Suppliers": [{"Successful_Bidder": "Acme", "Award_Amount": "5",
                          "Date_Awarded": "2025-02-02"}],
}
CKAN_AWARDED = {
    "Document Number": "3303123110", "RFx (Solicitation) Type": "RFQ",
    "High Level Category": "Goods", "Solicitation Document Description": "Widgets",
    "Division": "Fleet", "Buyer Name": "A Buyer", "Buyer Email": "b@toronto.ca",
    "Buyer Phone Number": "416", "Successful Supplier": "Acme", "Award": "100",
    "Award Authority Obtained Date": "2025-02-02", "_id": 1,
}
CKAN_OPEN = {
    "Document Number": "5749398870", "RFx (Solicitation) Type": "RFQ",
    "NOIP (Notice of Intended Procurement) Type": "", "Issue Date": "2025-01-01",
    "Submission Deadline": "2025-02-01", "High Level Category": "Goods",
    "Solicitation Document Description": "Widgets", "Division": "Fleet",
    "Buyer Name": "A Buyer", "Buyer Email": "b@toronto.ca",
    "Buyer Phone Number": "416", "Wards": "All", "_id": 1,
}
CKAN_NONCOMP = {
    "Workspace Number": "WS-1", "Supplier Name": "Acme", "Reason": "Sole source",
    "Contract Amount": "5", "Contract Date": "2025-02-02", "Division": "Fleet", "_id": 1,
}


class FakeHttp:
    """Serves each feed, matching the real request/response shapes in odata.py and ckan.py.

    Each feed takes a dict (one record), a list (several — the OData feeds are sampled),
    or {} / [] for a feed that returns nothing. Pages honestly, so fetch_entityset's
    $skip loop and fetch_datastore's offset loop actually terminate.
    """

    def __init__(self, solicitation=None, noncomp=None, awarded=None, open_=None, ckan_noncomp=None):
        def page(value, default):
            value = default if value is None else value
            return [value] if isinstance(value, dict) and value else list(value or [])

        self.records = {
            "solicitation": page(solicitation, ODATA_SOLICITATION),
            "noncomp": page(noncomp, ODATA_NONCOMP),
            "awarded": page(awarded, CKAN_AWARDED),
            "open": page(open_, CKAN_OPEN),
            "ckan_noncomp": page(ckan_noncomp, CKAN_NONCOMP),
        }

    def get_json(self, url, params=None):
        params = params or {}
        if "package_show" in url:  # CKAN slug -> resource id; slug round-trips as the id
            return {"result": {"resources": [{"datastore_active": True, "id": params["id"]}]}}
        if "datastore_search" in url:
            key = {config.CKAN_AWARDED_SLUG: "awarded",
                   config.CKAN_OPEN_SLUG: "open",
                   config.CKAN_NONCOMP_SLUG: "ckan_noncomp"}[params["resource_id"]]
            offset, limit = params["offset"], params["limit"]
            return {"result": {"records": self.records[key][offset:offset + limit]}}
        key = "noncomp" if config.ODATA_NONCOMPETITIVE in url else "solicitation"
        skip, top = params["$skip"], params["$top"]
        return {"value": self.records[key][skip:skip + top]}


def _run():
    return SchemaCheckSource().fetch(FakeHttp())


def test_passes_when_every_feed_still_has_the_fields_we_read():
    assert list(_run()) == []  # no drift, no rows


def test_detects_a_renamed_odata_field():
    drifted = {**ODATA_SOLICITATION}
    drifted["Posting_Title_v2"] = drifted.pop("Posting_Title")  # the City renames a field
    with pytest.raises(ValueError, match="Posting_Title"):
        SchemaCheckSource().fetch(FakeHttp(solicitation=drifted))


def test_detects_a_renamed_ckan_column():
    drifted = {**CKAN_AWARDED}
    drifted["Winning Supplier"] = drifted.pop("Successful Supplier")
    with pytest.raises(ValueError, match="Successful Supplier"):
        SchemaCheckSource().fetch(FakeHttp(awarded=drifted))


def test_detects_drift_in_the_nested_supplier_dict():
    drifted = {**ODATA_SOLICITATION, "Awarded_Suppliers": [{"Bidder": "Acme"}]}
    # match the nested field, not "Awarded_Suppliers" — that also matches the top-level message
    with pytest.raises(ValueError, match="Successful_Bidder"):
        SchemaCheckSource().fetch(FakeHttp(solicitation=drifted))


def test_nested_check_looks_past_records_that_have_no_supplier_yet():
    """The live feed's record #1 has no Awarded_Suppliers (it isn't awarded yet), so a
    check that only sampled record #1 would silently verify none of the nested fields."""
    not_awarded = {**ODATA_SOLICITATION, "Awarded_Suppliers": []}
    drifted_award = {**ODATA_SOLICITATION, "Awarded_Suppliers": [{"Bidder": "Acme"}]}
    with pytest.raises(ValueError, match="Successful_Bidder"):
        SchemaCheckSource().fetch(FakeHttp(solicitation=[not_awarded, drifted_award]))


def test_reports_when_no_sampled_record_has_a_supplier_to_check():
    # Can't verify != verified. Silence here would hide the nested fields forever.
    not_awarded = {**ODATA_SOLICITATION, "Awarded_Suppliers": []}
    with pytest.raises(ValueError, match="went unchecked"):
        SchemaCheckSource().fetch(FakeHttp(solicitation=[not_awarded]))


def test_reports_every_drifted_feed_not_just_the_first():
    bad_odata = {k: v for k, v in ODATA_SOLICITATION.items() if k != "Status"}
    bad_ckan = {k: v for k, v in CKAN_NONCOMP.items() if k != "Reason"}
    with pytest.raises(ValueError) as exc:
        SchemaCheckSource().fetch(FakeHttp(solicitation=bad_odata, ckan_noncomp=bad_ckan))
    assert "Status" in str(exc.value) and "Reason" in str(exc.value)


def test_detects_a_feed_that_returns_nothing():
    with pytest.raises(ValueError, match="no records"):
        SchemaCheckSource().fetch(FakeHttp(open_={}))


def test_declared_schema_matches_what_the_normalizers_actually_read():
    """The declared fields mirror the normalizers' .get("...") calls by hand. This fails
    when the two diverge — otherwise someone adds raw.get("New_Field"), forgets to declare
    it, and drift in that field silently stops being detected: #10's bug, quietly restored.
    """
    import ast
    import pathlib

    from toronto_bids.sources import schema_check

    # (module, normalizer) -> the feed whose declared fields that normalizer reads.
    reads = {
        ("odata.py", "normalize_solicitation"): "odata_solicitations",
        ("odata.py", "normalize_noncompetitive"): "odata_noncompetitive",
        ("ckan.py", "normalize_awarded"): "ckan_awarded",
        ("ckan.py", "normalize_open"): "ckan_open",
        ("ckan.py", "normalize_noncompetitive"): "ckan_noncomp",
    }
    src_dir = pathlib.Path(schema_check.__file__).parent
    for (module, func_name), feed in reads.items():
        tree = ast.parse((src_dir / module).read_text())
        func = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == func_name)
        # every string literal passed to a .get(...) inside the normalizer
        actually_read = {
            n.args[0].value for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "get" and n.args and isinstance(n.args[0], ast.Constant)
        }
        if feed in schema_check._ODATA:
            _, top, supplier = schema_check._ODATA[feed]
            declared = top | supplier
        else:
            _, declared = schema_check._CKAN[feed]
        assert actually_read == declared, (
            f"{module}:{func_name} and the declared schema for {feed} have diverged.\n"
            f"  read but not declared (drift here goes undetected): {sorted(actually_read - declared)}\n"
            f"  declared but not read (stale entry): {sorted(declared - actually_read)}"
        )


def test_unused_extra_fields_are_not_drift():
    # The City adding a field is not a problem; only losing one we read is.
    assert missing_fields("f", {"a": 1, "b": 2, "new": 3}, {"a", "b"}) == []


def test_schema_check_runs_in_the_default_pipeline():
    assert "schema_check" in [s.name for s in pipeline.default_sources()]


def test_drift_is_isolated_and_surfaced_not_swallowed(conn):
    """Drift must fail loudly (#18) yet never stop the other sources ingesting (#10)."""
    from tests.test_pipeline import FakeSource
    from toronto_bids.models import Solicitation
    from toronto_bids.store import db

    drifted = {k: v for k, v in ODATA_SOLICITATION.items() if k != "Status"}
    real_data = FakeSource("odata_solicitations", [Solicitation("3303123110", source="odata")])
    failures = pipeline.sync(conn, http=FakeHttp(solicitation=drifted),
                             sources=[SchemaCheckSource(), real_data])
    assert [name for name, _ in failures] == ["schema_check"]  # surfaced to the CLI
    assert "Status" in failures[0][1]
    assert db.counts(conn)["solicitation"] == 1  # ingestion continued anyway
