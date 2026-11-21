import pytest

from toronto_bids.sources.ckan import CkanSource, normalize_capital_project
from toronto_bids.store import db

RAW = {
    "_id": "1",
    "No.": "2",
    "Name and Construction Contract Number":
        "Dufferin Transfer Station - Compactors Replacement  - 25ECS-MI-02SW",
    "Type of Work": "Construction",
    "Scope of Work: Detailed Description": "Replace the compactors at Dufferin.",
    "Delivery Division": "Solid Waste Management Services",
    "Project Owner (Division)": "Solid Waste Management Services",
    "Target Sourcing Year": "2026",
    "Target Award Year": "2026",
    "Sourcing Type": "RFT",
    "Estimated Range": "Up to $5M",
    "Estimated Contract Term (Months)": "24",
}


def test_normalizes_a_pipeline_row():
    project, = normalize_capital_project(RAW)
    assert project.name == RAW["Name and Construction Contract Number"]
    assert project.type_of_work == "Construction"
    assert project.target_sourcing_year == "2026"
    assert project.sourcing_type == "RFT"
    assert project.estimated_range == "Up to $5M"
    assert project.source == "ckan_pipeline"


@pytest.mark.parametrize("name,expected", [
    ("Dufferin Transfer Station - Compactors Replacement  - 25ECS-MI-02SW", "25ECS-MI-02SW"),
    ("Dufferin Transfer Station - Paving - 25SWM-IRM-042CDU", "25SWM-IRM-042CDU"),
    ("Basement Flooding Protection Program - 23ECS-LU-02FP", "23ECS-LU-02FP"),
    ("Sewage Pumping Station Planning and Environmental Assessment Study", None),
])
def test_teases_the_construction_contract_number_out_of_the_name(name, expected):
    project, = normalize_capital_project({"Name and Construction Contract Number": name})
    assert project.contract_number == expected


def test_a_row_with_no_name_is_skipped():
    assert list(normalize_capital_project({"Type of Work": "Construction"})) == []


def test_stores_and_is_idempotent(conn):
    project, = normalize_capital_project(RAW)
    db.upsert_row(conn, project, overwrite=True)
    db.upsert_row(conn, project, overwrite=True)
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM capital_project").fetchone()[0] == 1
    assert db.counts(conn)["capital_project"] == 1


def test_a_refreshed_project_updates_rather_than_being_coalesced_away(conn):
    first, = normalize_capital_project(RAW)
    db.upsert_row(conn, first, overwrite=True)
    moved, = normalize_capital_project(dict(RAW, **{"Target Sourcing Year": "2027"}))
    db.upsert_row(conn, moved, overwrite=True)
    conn.commit()
    assert conn.execute("SELECT target_sourcing_year FROM capital_project").fetchone()[0] == "2027"


def test_the_pipeline_source_overwrites_unlike_the_other_ckan_sources():
    from toronto_bids import config, pipeline

    by_name = {s.name: s for s in pipeline.default_sources()}
    assert by_name["ckan_pipeline"].overwrite is True
    assert by_name["ckan_awarded"].overwrite is False
    assert by_name["ckan_open"].overwrite is False
    assert CkanSource(name="x", slug=config.CKAN_PIPELINE_SLUG,
                      kind="capital_pipeline").overwrite is False


def test_capital_projects_reach_the_export(conn):
    from toronto_bids.export.document import build_export_document

    project, = normalize_capital_project(RAW)
    db.upsert_row(conn, project, overwrite=True)
    conn.commit()
    doc = build_export_document(conn, generated_at="2026-07-16T00:00:00Z")
    assert len(doc["capital_projects"]) == 1
    assert doc["capital_projects"][0]["contract_number"] == "25ECS-MI-02SW"
    assert doc["meta"]["counts"]["capital_project"] == 1
