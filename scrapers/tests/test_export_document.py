import json
import pathlib

import pytest

from toronto_bids.export.document import _parse_json, build_export_document
from toronto_bids.export.json_export import export_json
from toronto_bids.models import (
    AribaAttachment,
    AribaPosting,
    Award,
    BackgroundPdf,
    Bid,
    CapitalProject,
    CouncilItem,
    NonCompetitive,
    Solicitation,
    Supplier,
    SuspendedFirm,
)
from toronto_bids.store import db


@pytest.fixture
def seeded(conn):
    db.upsert_row(conn, Solicitation(document_number="5672751291", status="Open",
                                     title="RFT Watermain", source="odata"), overwrite=True)
    db.upsert_row(conn, Award(document_number="5672751291", supplier_name_raw="Acme Co",
                              award_amount="1000", source="odata"), overwrite=True)
    db.upsert_row(conn, AribaPosting(rfx_id="1110015885", document_number="5672751291",
                                     title="RFT Watermain", categories='["Sidewalk"]',
                                     raw_json='{"big":"blob"}', source="ariba_discovery"), overwrite=True)
    db.upsert_row(conn, AribaPosting(rfx_id="1110099999", document_number=None,
                                     title="Unbridged posting", categories='["Water"]',
                                     source="ariba_discovery"), overwrite=True)
    db.upsert_row(conn, NonCompetitive(workspace_number="8614", supplier_name_raw="Sole Source Inc",
                                       reason="Emergency", source="odata"), overwrite=True)
    conn.commit()
    return conn


def test_meta_carries_the_attribution_the_grants_require(conn):
    doc = build_export_document(conn, generated_at="2026-07-15T00:00:00Z")
    attribution = doc["meta"]["attribution"]
    by_slug = {a["slug"]: a for a in attribution}
    assert set(by_slug) >= {"trca", "toronto-zoo"}
    assert "Toronto and Region Conservation Authority" in by_slug["trca"]["statement"]
    assert "Toronto Zoo" in by_slug["toronto-zoo"]["statement"]
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    for entry in attribution:
        assert entry["statement"] and entry["body"] and entry["source_url"]
        assert (repo_root / entry["permission"]).is_file()
    assert [a["slug"] for a in attribution] == sorted(by_slug)
    assert build_export_document(conn, generated_at="2026-07-15T00:00:00Z") == doc


def test_meta_has_generated_at_counts_and_sources(seeded):
    db.finish_sync_run(seeded, db.start_sync_run(seeded, "odata_solicitations"),
                       status="ok", rows_fetched=5, rows_upserted=5)
    doc = build_export_document(seeded, generated_at="2026-07-15T00:00:00Z")
    assert doc["meta"]["generated_at"] == "2026-07-15T00:00:00Z"
    assert doc["meta"]["counts"]["solicitation"] == 1
    assert doc["meta"]["sources"][-1]["source"] == "odata_solicitations"
    assert doc["meta"]["sources"][-1]["status"] == "ok"


def test_award_and_posting_nested_under_solicitation(seeded):
    doc = build_export_document(seeded, generated_at="t")
    sols = doc["solicitations"]
    assert len(sols) == 1
    sol = sols[0]
    assert sol["document_number"] == "5672751291"
    assert "odata_id" not in sol
    assert len(sol["awards"]) == 1
    assert sol["awards"][0]["supplier_name_raw"] == "Acme Co"
    assert "id" not in sol["awards"][0]
    assert "document_number" not in sol["awards"][0]
    assert len(sol["ariba_postings"]) == 1
    assert sol["ariba_postings"][0]["rfx_id"] == "1110015885"
    assert "raw_json" not in sol["ariba_postings"][0]
    assert sol["ariba_postings"][0]["categories"] == ["Sidewalk"]
    assert "document_number" not in sol["ariba_postings"][0]


def test_unbridged_posting_goes_to_unlinked_not_dropped(seeded):
    doc = build_export_document(seeded, generated_at="t")
    unlinked = doc["unlinked_ariba_postings"]
    assert len(unlinked) == 1
    assert unlinked[0]["rfx_id"] == "1110099999"
    assert unlinked[0]["categories"] == ["Water"]
    assert "raw_json" not in unlinked[0]
    assert all(p["rfx_id"] != "1110099999"
               for s in doc["solicitations"] for p in s["ariba_postings"])


def test_noncompetitive_is_separate_top_level(seeded):
    doc = build_export_document(seeded, generated_at="t")
    assert len(doc["noncompetitive"]) == 1
    assert doc["noncompetitive"][0]["workspace_number"] == "8614"
    assert "supplier_id" in doc["noncompetitive"][0]


def test_empty_store_produces_empty_collections(conn):
    doc = build_export_document(conn, generated_at="t")
    assert doc["solicitations"] == []
    assert doc["noncompetitive"] == []
    assert doc["suspended_firms"] == []
    assert doc["suppliers"] == []
    assert doc["council_items"] == []
    assert doc["unlinked_ariba_postings"] == []
    assert doc["unlinked_awards"] == []
    assert doc["unlinked_bids"] == []
    assert doc["meta"]["counts"]["solicitation"] == 0


def test_orphan_posting_docnum_not_in_solicitation_goes_to_unlinked(seeded):
    db.upsert_row(seeded, AribaPosting(rfx_id="1110088888", document_number="4044346425",
                                       title="Mock RFT", source="ariba_discovery"), overwrite=True)
    seeded.commit()
    doc = build_export_document(seeded, generated_at="t")
    orphan = [p for p in doc["unlinked_ariba_postings"] if p["rfx_id"] == "1110088888"]
    assert len(orphan) == 1
    assert orphan[0]["document_number"] == "4044346425"
    assert all(p["rfx_id"] != "1110088888"
               for s in doc["solicitations"] for p in s["ariba_postings"])


def test_orphan_award_docnum_not_in_solicitation_goes_to_unlinked_awards(seeded):
    db.upsert_row(seeded, Award(document_number="4044346425", supplier_name_raw="Orphan Co",
                                source="ckan_awarded"), overwrite=True)
    seeded.commit()
    doc = build_export_document(seeded, generated_at="t")
    orphan = [a for a in doc["unlinked_awards"] if a["supplier_name_raw"] == "Orphan Co"]
    assert len(orphan) == 1
    assert orphan[0]["document_number"] == "4044346425"


def test_no_record_is_dropped_counts_reconcile(seeded):
    db.upsert_row(seeded, AribaPosting(rfx_id="1110088888", document_number="4044346425",
                                       source="ariba_discovery"), overwrite=True)
    db.upsert_row(seeded, Award(document_number="4044346425", supplier_name_raw="Orphan Co",
                                source="ckan_awarded"), overwrite=True)
    seeded.commit()
    doc = build_export_document(seeded, generated_at="t")
    counts = doc["meta"]["counts"]
    nested_postings = sum(len(s["ariba_postings"]) for s in doc["solicitations"])
    assert nested_postings + len(doc["unlinked_ariba_postings"]) == counts["ariba_posting"]
    nested_awards = sum(len(s["awards"]) for s in doc["solicitations"])
    assert nested_awards + len(doc["unlinked_awards"]) == counts["award"]


def test_suspended_firms_is_separate_top_level(conn):
    db.upsert_row(conn, SuspendedFirm(supplier_name_raw="Duron Ontario Ltd.", status="Suspended",
                                      council_authority="2025.GG19.17", source="suspended_firms"),
                  overwrite=True)
    conn.commit()
    doc = build_export_document(conn, generated_at="t")
    assert len(doc["suspended_firms"]) == 1
    firm = doc["suspended_firms"][0]
    assert firm["supplier_name_raw"] == "Duron Ontario Ltd."
    assert firm["council_authority"] == "2025.GG19.17"
    assert "id" not in firm


def test_export_has_suppliers_array_and_retains_supplier_id(conn):
    db.upsert_row(conn, Supplier(supplier_key="compugen inc", display_name="Compugen Inc.",
                                 variants='["Compugen Inc."]'), overwrite=True)
    sid = conn.execute("SELECT supplier_id FROM supplier WHERE supplier_key='compugen inc'").fetchone()[0]
    db.upsert_row(conn, Solicitation(document_number="3303123110", source="odata"), overwrite=True)
    db.upsert_row(conn, Award(document_number="3303123110", supplier_name_raw="Compugen Inc.",
                              source="odata"), overwrite=True)
    conn.execute("UPDATE award SET supplier_id=? WHERE document_number='3303123110'", (sid,))
    conn.commit()

    doc = build_export_document(conn, generated_at="t")
    assert len(doc["suppliers"]) == 1
    assert doc["suppliers"][0]["display_name"] == "Compugen Inc."
    assert doc["suppliers"][0]["supplier_key"] == "compugen inc"
    assert doc["suppliers"][0]["variants"] == ["Compugen Inc."]
    award = doc["solicitations"][0]["awards"][0]
    assert award["supplier_id"] == sid


def test_capital_projects_reach_the_export(conn):
    db.upsert_row(conn, CapitalProject(
        name="Dufferin Transfer Station - Compactors Replacement  - 25ECS-MI-02SW",
        contract_number="25ECS-MI-02SW",
        type_of_work="Construction",
        target_sourcing_year="2026",
        source="ckan_pipeline",
    ), overwrite=True)
    conn.commit()
    doc = build_export_document(conn, generated_at="2026-07-16T00:00:00Z")
    assert len(doc["capital_projects"]) == 1
    assert doc["capital_projects"][0]["contract_number"] == "25ECS-MI-02SW"
    assert doc["meta"]["counts"]["capital_project"] == 1


def test_export_has_council_items_with_nested_pdfs(conn):
    db.upsert_row(conn, CouncilItem(reference="2025.GG26.3", title="Suspension",
                                    decision_text="Adopted."), overwrite=True)
    db.upsert_row(conn, BackgroundPdf(url="https://x/bgrd/backgroundfile-260581.pdf",
                                      reference="2025.GG26.3", kind="bgrd", text="REPORT",
                                      local_path="/abs/path/backgroundfile-260581.pdf"),
                  overwrite=True)
    conn.commit()
    doc = build_export_document(conn, generated_at="t")
    assert len(doc["council_items"]) == 1
    ci = doc["council_items"][0]
    assert ci["reference"] == "2025.GG26.3"
    assert len(ci["background_pdfs"]) == 1
    assert ci["background_pdfs"][0]["kind"] == "bgrd"
    assert "text" not in ci["background_pdfs"][0]
    assert "local_path" not in ci["background_pdfs"][0]


def test_documents_nested_under_solicitation(seeded):
    db.upsert_row(seeded, AribaAttachment(
        document_number="5672751291", filename="site-plan.pdf",
        path="Appendix C2 - Planning Documents.zip/site-plan.pdf",
        file_size=12656277, crc32="deadbeef", zip_name="Doc5672751291.zip",
        zip_sha256="a" * 64), overwrite=True)
    db.upsert_row(seeded, BackgroundPdf(
        url="https://secure.toronto.ca/c3api_upload/retrieve/pmmd_solicitations/binid",
        document_number="5672751291", kind="award_summary",
        local_path="/x/binid", sha256="b" * 64, text="..."), overwrite=True)
    seeded.commit()

    sol = next(s for s in build_export_document(seeded, generated_at="t")["solicitations"]
               if s["document_number"] == "5672751291")
    docs = {d["name"]: d for d in sol["documents"]}

    ariba = docs["site-plan.pdf"]
    assert ariba["source"] == "ariba_attachment"
    assert ariba["path"] == "Appendix C2 - Planning Documents.zip/site-plan.pdf"
    assert ariba["type"] == "pdf" and ariba["size_bytes"] == 12656277 and ariba["url"] is None
    assert "crc32" not in ariba and "sha256" not in ariba

    form = docs["Award Summary Form.pdf"]
    assert form["source"] == "award_summary" and form["type"] == "pdf"
    assert form["size_bytes"] is None
    assert form["url"].startswith("https://secure.toronto.ca/")


def test_documents_use_leaf_basename_for_name_and_type(seeded):
    db.upsert_row(seeded, AribaAttachment(
        document_number="5672751291", filename="README",
        path="Base Information.zip/README",
        file_size=42, crc32="deadbeef", zip_name="Doc5672751291.zip",
        zip_sha256="a" * 64), overwrite=True)
    db.upsert_row(seeded, AribaAttachment(
        document_number="5672751291", filename="site-plan.pdf",
        path="Appendix C2.zip/drawings/site-plan.pdf",
        file_size=99, crc32="beefdead", zip_name="Doc5672751291.zip",
        zip_sha256="b" * 64), overwrite=True)
    seeded.commit()

    sol = next(s for s in build_export_document(seeded, generated_at="t")["solicitations"]
               if s["document_number"] == "5672751291")
    docs = {d["path"]: d for d in sol["documents"]}

    readme = docs["Base Information.zip/README"]
    assert readme["name"] == "README"
    assert readme["type"] is None

    drawing = docs["Appendix C2.zip/drawings/site-plan.pdf"]
    assert drawing["name"] == "site-plan.pdf"
    assert drawing["type"] == "pdf"


def test_solicitation_without_documents_gets_empty_list(conn):
    db.upsert_row(conn, Solicitation(document_number="1", status="Open", source="odata"),
                  overwrite=True)
    conn.commit()
    sol = build_export_document(conn, generated_at="t")["solicitations"][0]
    assert sol["documents"] == []


def test_staff_report_surfaces_under_solicitation_via_bid_bridge(seeded):
    db.upsert_row(seeded, Bid(bidder_name_raw="Acme Co", reference="2020.BA5.3",
                              document_number="5672751291", bid_price="1000",
                              source="bid_award_panel"), overwrite=True)
    db.upsert_row(seeded, BackgroundPdf(
        url="https://www.toronto.ca/legdocs/mmis/2020/ba/bgrd/backgroundfile-99644.pdf",
        reference="2020.BA5.3", kind="bgrd"), overwrite=True)
    seeded.commit()

    sol = next(s for s in build_export_document(seeded, generated_at="t")["solicitations"]
               if s["document_number"] == "5672751291")
    report = next(d for d in sol["documents"] if d["source"] == "staff_report")
    assert report["name"] == "backgroundfile-99644.pdf"
    assert report["path"] == "backgroundfile-99644.pdf"
    assert report["type"] == "pdf"
    assert report["size_bytes"] is None
    assert report["url"] == "https://www.toronto.ca/legdocs/mmis/2020/ba/bgrd/backgroundfile-99644.pdf"
    assert set(report) == {"source", "name", "path", "type", "size_bytes", "url"}


def test_reference_null_bid_nests_under_solicitation(seeded):
    db.upsert_row(seeded, Bid(bidder_name_raw="Post-Panel Co", reference=None,
                              document_number="5672751291", bid_price="2500",
                              source="award_summary"), overwrite=True)
    seeded.commit()

    doc = build_export_document(seeded, generated_at="t")
    sol = next(s for s in doc["solicitations"] if s["document_number"] == "5672751291")
    assert len(sol["bids"]) == 1
    assert sol["bids"][0]["bidder_name_raw"] == "Post-Panel Co"
    assert "id" not in sol["bids"][0]
    assert "document_number" not in sol["bids"][0]
    assert all(b["bidder_name_raw"] != "Post-Panel Co"
               for ci in doc["council_items"] for b in ci["bids"])


def test_reference_null_bid_with_no_matching_solicitation_goes_to_unlinked_bids(seeded):
    db.upsert_row(seeded, Bid(bidder_name_raw="Orphan Bidder", reference=None,
                              document_number="4044346425", bid_price="500",
                              source="award_summary"), overwrite=True)
    seeded.commit()
    doc = build_export_document(seeded, generated_at="t")
    orphan = [b for b in doc["unlinked_bids"] if b["bidder_name_raw"] == "Orphan Bidder"]
    assert len(orphan) == 1
    assert orphan[0]["document_number"] == "4044346425"
    assert all(b["bidder_name_raw"] != "Orphan Bidder"
               for s in doc["solicitations"] for b in s["bids"])


def test_reference_bid_stays_under_council_item(conn):
    db.upsert_row(conn, CouncilItem(reference="2025.BA5.3", title="Award"), overwrite=True)
    db.upsert_row(conn, Bid(bidder_name_raw="Panel Bidder", reference="2025.BA5.3",
                            bid_price="100", source="bid_award_panel"), overwrite=True)
    conn.commit()
    doc = build_export_document(conn, generated_at="t")
    ci = doc["council_items"][0]
    assert [b["bidder_name_raw"] for b in ci["bids"]] == ["Panel Bidder"]
    assert doc["unlinked_bids"] == []


def test_no_bid_is_dropped_counts_reconcile(seeded):
    db.upsert_row(seeded, CouncilItem(reference="2020.BA5.3", title="Award"), overwrite=True)
    db.upsert_row(seeded, Bid(bidder_name_raw="Panel Co", reference="2020.BA5.3",
                              bid_price="100", source="bid_award_panel"), overwrite=True)
    db.upsert_row(seeded, Bid(bidder_name_raw="Nested Co", reference=None,
                              document_number="5672751291", bid_price="200",
                              source="award_summary"), overwrite=True)
    db.upsert_row(seeded, Bid(bidder_name_raw="Orphan Co", reference=None,
                              document_number="4044346425", bid_price="300",
                              source="award_summary"), overwrite=True)
    seeded.commit()
    doc = build_export_document(seeded, generated_at="t")
    counts = doc["meta"]["counts"]
    council = sum(len(ci["bids"]) for ci in doc["council_items"])
    nested = sum(len(s["bids"]) for s in doc["solicitations"])
    assert council + nested + len(doc["unlinked_bids"]) == counts["bid"]


def test_pre_ariba_bid_bridges_to_its_solicitation(seeded):
    db.upsert_row(seeded, CouncilItem(reference="2016.BD106.3", title="Award"), overwrite=True)
    db.upsert_row(seeded, Bid(bidder_name_raw="Loser Co", reference="2016.BD106.3",
                              document_number=None, bid_price="9", source="bid_award_panel"), overwrite=True)
    seeded.execute("INSERT INTO solicitation_link (reference, document_number, method) "
                   "VALUES ('2016.BD106.3', '5672751291', 'council_pre_ariba')")
    seeded.commit()
    doc = build_export_document(seeded, generated_at="t")
    sol = next(s for s in doc["solicitations"] if s["document_number"] == "5672751291")
    assert any(b["bidder_name_raw"] == "Loser Co" for b in sol["bids"])
    ci = next(c for c in doc["council_items"] if c["reference"] == "2016.BD106.3")
    assert all(b["bidder_name_raw"] != "Loser Co" for b in ci["bids"])
    council = sum(len(c["bids"]) for c in doc["council_items"])
    nested = sum(len(s["bids"]) for s in doc["solicitations"])
    assert council + nested + len(doc["unlinked_bids"]) == doc["meta"]["counts"]["bid"]


def test_ambiguous_council_reference_bridge_does_not_misattach_reports_or_bids(seeded):
    db.upsert_row(seeded, Solicitation(document_number="5672751292", status="Open",
                                       title="Second Solicitation", source="odata"), overwrite=True)
    db.upsert_row(seeded, CouncilItem(reference="2020.BA9.1", title="Multi-Award"), overwrite=True)
    db.upsert_row(seeded, BackgroundPdf(
        url="https://www.toronto.ca/legdocs/mmis/2020/ba/bgrd/backgroundfile-ambiguous.pdf",
        reference="2020.BA9.1", kind="bgrd"), overwrite=True)
    db.upsert_row(seeded, Bid(bidder_name_raw="Doc1 Bidder", reference="2020.BA9.1",
                              document_number="5672751291", bid_price="100", source="bid_award_panel"), overwrite=True)
    db.upsert_row(seeded, Bid(bidder_name_raw="Ambiguous Bidder", reference="2020.BA9.1",
                              document_number=None, bid_price="500", source="bid_award_panel"), overwrite=True)
    seeded.execute(
        "INSERT INTO solicitation_link (reference, document_number, method) "
        "VALUES ('2020.BA9.1', '5672751292', 'council_pre_ariba')"
    )
    seeded.commit()

    doc = build_export_document(seeded, generated_at="t")
    for sol in doc["solicitations"]:
        assert all(d["url"] != "https://www.toronto.ca/legdocs/mmis/2020/ba/bgrd/backgroundfile-ambiguous.pdf"
                   for d in sol["documents"])
        assert all(b["bidder_name_raw"] != "Ambiguous Bidder" for b in sol["bids"])
    ci = next(c for c in doc["council_items"] if c["reference"] == "2020.BA9.1")
    assert any(b["bidder_name_raw"] == "Ambiguous Bidder" for b in ci["bids"])


def test_unbridged_staff_report_stays_out_of_documents(seeded):
    db.upsert_row(seeded, BackgroundPdf(
        url="https://www.toronto.ca/legdocs/x/backgroundfile-1.pdf",
        reference="2020.XX9.9", kind="bgrd"), overwrite=True)
    seeded.commit()

    sols = build_export_document(seeded, generated_at="t")["solicitations"]
    assert sols
    assert not any(d["source"] == "staff_report"
                   and d["url"] == "https://www.toronto.ca/legdocs/x/backgroundfile-1.pdf"
                   for s in sols for d in s["documents"])


def test_dual_key_bid_stays_under_council_item_not_solicitation(seeded):
    db.upsert_row(seeded, CouncilItem(reference="2020.BA5.3", title="Award"), overwrite=True)
    db.upsert_row(seeded, Bid(bidder_name_raw="Dual Key Co", reference="2020.BA5.3",
                              document_number="5672751291", bid_price="42",
                              source="bid_award_panel"), overwrite=True)
    seeded.commit()

    doc = build_export_document(seeded, generated_at="t")
    ci = next(c for c in doc["council_items"] if c["reference"] == "2020.BA5.3")
    assert any(b["bidder_name_raw"] == "Dual Key Co" for b in ci["bids"])

    sol = next(s for s in doc["solicitations"] if s["document_number"] == "5672751291")
    assert all(b["bidder_name_raw"] != "Dual Key Co" for b in sol["bids"])

    counts = doc["meta"]["counts"]
    council = sum(len(c["bids"]) for c in doc["council_items"])
    nested = sum(len(s["bids"]) for s in doc["solicitations"])
    assert council + nested + len(doc["unlinked_bids"]) == counts["bid"]


def test_parse_json_leaves_invalid_json_intact():
    assert _parse_json({"categories": "{not-valid-json"}, "categories") == {
        "categories": "{not-valid-json"
    }
    assert _parse_json({"categories": None}, "categories") == {"categories": None}


def test_export_writes_valid_json_file(conn, tmp_path):
    db.upsert_row(conn, Solicitation(document_number="5672751291", title="RFT", source="odata"),
                  overwrite=True)
    conn.commit()
    out = tmp_path / "nested" / "bids.json"
    result = export_json(conn, out, generated_at="2026-07-15T00:00:00Z")
    assert result == out
    assert out.exists()
    doc = json.loads(out.read_text())
    assert doc["meta"]["generated_at"] == "2026-07-15T00:00:00Z"
    assert doc["solicitations"][0]["document_number"] == "5672751291"


def test_export_writes_utf8_content(conn, tmp_path):
    db.upsert_row(conn, Solicitation(document_number="5672751291", title="Café Réno",
                                     source="odata"), overwrite=True)
    conn.commit()
    out = tmp_path / "bids.json"
    export_json(conn, out, generated_at="t")
    assert "Café Réno" in out.read_text(encoding="utf-8")
