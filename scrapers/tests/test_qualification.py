"""Independent relations, coverage and evidence-bound count reviews (#302)."""

import copy
import json
from itertools import count
from pathlib import Path

import pytest

from toronto_bids.extract import EXTRACTOR_VERSION, build_prompt
from toronto_bids.qualification import (
    REQUIRED_CORPORA, _amount_values, count_discrepancy_report, digest, identity,
    load_cohort, qualification_report, relationships, score,
)
from toronto_bids.store.db import mark_extracted


def relation(**changes):
    return {"supplier": "Supplier Ltd.", "contract": "123", "stage": "single_stage",
            "role": "bid", "amount": "$10", "basis": "plus_HST", "status": "not_stated", **changes}


def extraction():
    return {"contracts": [{"reference": "123", "stage": "single_stage",
                            "bids": [{"supplier_name": "Supplier Ltd.", "amount_raw": "$10",
                                      "amount_basis": "plus_HST", "status": "not_stated"}],
                            "awards": []}]}


def test_full_relations_distinguish_false_joins_attributes_fabrications_and_duplicates():
    expected = [relation(), relation(contract="456", amount=None, status="disqualified")]
    actual = [relation(), relation(), relation(contract="789"),
              relation(contract="456", amount="$20"), relation(supplier="Invented Inc.")]
    result = score(expected, actual)
    assert result["correct"] == 1 and result["expected"] == 2 and result["observed"] == 5
    assert result["precision"] == 0.2 and result["recall"] == 0.5
    assert all(len(result[k]) == 1 for k in
               ("omissions", "false_joins", "attribute_errors", "fabrications", "duplicates"))


def test_amount_value_diagnostics_do_not_erase_raw_qualifiers_or_change_exact_score():
    expected = [relation(amount='$1,000.00')]
    actual = [relation(amount='$1,000.00*')]
    assert score(expected, actual)['correct'] == 0
    assert score(_amount_values(expected), _amount_values(actual))['correct'] == 1
    # A suffix identifying a rate or foreign currency is not a footnote or CAD amount.
    for amount in ['$1,000.00 USD', '$1,000.00/MT', '$1,000.01']:
        assert score(_amount_values(expected), _amount_values([relation(amount=amount)]))['correct'] == 0


@pytest.mark.parametrize("change", [dict(stage="prequalification_then_tender"), dict(role="award"),
                                      dict(basis="including_HST"), dict(status="no_bid"), dict(amount=None)])
def test_each_relationship_field_is_load_bearing(change):
    result = score([relation()], [relation(**change)])
    assert result["correct"] == 0 and len(result["omissions"]) == 1


def test_prequalification_and_invitation_are_separate_from_bids_and_awards():
    value = extraction()
    value["contracts"][0].update(prequalified=["Supplier Ltd."], invited_to_tender=["Other Ltd."])
    assert [r["role"] for r in relationships(value)] == ["bid", "prequalified", "invited_to_tender"]
    value["contracts"][0]["prequalified"] = None
    with pytest.raises(ValueError):
        relationships(value)


@pytest.fixture
def cohort(tmp_path):
    text = "Supplier Ltd. submitted $10."
    (tmp_path / "source.txt").write_text(text)
    threshold = {"precision": 1.0, "recall": 1.0, "modes": ["sync", "batch"], "difficult_repeats": 2}
    doc = {"id": "sample", "corpus": "ep", "text_path": "source.txt", "text_sha256": digest(text.encode()),
           "document_sha256": "a" * 64, "review_status": "reviewed", "contracts": ["123"],
           "relations": [{**relation(), "evidence": [text]}], "review_notes": "Table and tax heading reviewed."}
    data = {"version": 1, "thresholds": {c: threshold for c in REQUIRED_CORPORA}, "documents": [doc]}
    path = tmp_path / "cohort.json"
    path.write_text(json.dumps(data))
    return path, data


_response_ids = count()


def attempt(doc, *, mode="sync", effort="high"):
    request = {"model": "claude-sonnet-5-5", "output_config": {"effort": effort, "format": {"schema": {}}},
               "messages": [{"role": "user", "content": [{"type": "text", "text": build_prompt("Supplier Ltd. submitted $10.")}]}]}
    result = extraction()
    response_id = f"response-{next(_response_ids)}"
    usage = {"input_tokens": 10, "output_tokens": 5}
    return {"state": "success", "extraction": result, "usage": {"input_tokens": 10, "output_tokens": 5},
            "charged_microdollars": 70 if mode == 'sync' else 35,
            'raw_response': {'id': response_id, 'model': request['model'], 'stop_reason': 'end_turn',
                             'content': [{'type': 'text', 'text': json.dumps(result)}], 'usage': usage},
            "provenance": {"document_sha256": doc["document_sha256"], "text_sha256": doc["text_sha256"],
                           "prompt_version": EXTRACTOR_VERSION, "model": request["model"], "effort": effort,
                           "schema_sha256": identity({}), "request_sha256": identity(request), "mode": mode,
                           "response_sha256": identity(result), "request": request,
                           "response_id": response_id}}


def run_file(path, id, attempts):
    result = path.parent / (id + ".json")
    result.write_text(json.dumps({"id": id, "attempts": attempts}))
    return result


def test_all_selected_documents_remain_visible_in_every_run(cohort):
    path, data = cohort
    runs = [run_file(path, "empty", {}), run_file(path, "pending", {"sample": {"state": "pending"}})]
    result = qualification_report(path, runs)
    assert result["selected"] == 1 and not result["qualified"]
    assert result["corpora"]["ep"]["states"] == {"missing": 1, "pending": 1}
    assert not result["corpora"]["ttc"]["qualified"]


def test_modes_must_share_identical_request_and_configuration(cohort):
    path, data = cohort
    doc = data["documents"][0]
    runs = [run_file(path, "sync", {"sample": attempt(doc)}),
            run_file(path, "batch", {"sample": attempt(doc, mode="batch", effort="medium")})]
    assert not qualification_report(path, runs)["corpora"]["ep"]["qualified"]
    runs[1] = run_file(path, "batch", {"sample": attempt(doc, mode="batch")})
    assert qualification_report(path, runs)["corpora"]["ep"]["qualified"]
    assert not qualification_report(path, runs)["qualified"]  # Other corpora absent.


def test_difficult_cases_require_independent_repeats_and_keep_failed_trials(cohort):
    path, data = cohort
    data["documents"][0]["difficult"] = True
    path.write_text(json.dumps(data))
    doc = data["documents"][0]
    runs = [run_file(path, str(i), {"sample": attempt(doc, mode=mode)})
            for i, mode in enumerate(["sync", "batch", "sync", "batch"])]
    assert qualification_report(path, runs)["corpora"]["ep"]["qualified"]
    runs.append(run_file(path, "failed", {"sample": {"state": "failed", "reason": "truncated"}}))
    assert not qualification_report(path, runs)["corpora"]["ep"]["qualified"]
    with pytest.raises(ValueError, match="unique"):
        qualification_report(path, [runs[0], runs[0]])


def test_unknown_historical_provenance_is_scored_but_never_qualified(cohort):
    path, data = cohort
    old = attempt(data["documents"][0])
    del old["provenance"]["request"]
    run = run_file(path, "old", {"sample": old})
    row = qualification_report(path, [run])["runs"][0]["documents"][0]
    assert row["metrics"]["correct"] == 1
    assert "exact request not retained" in row["provenance_issues"]


def test_request_cannot_claim_different_input_text(cohort):
    path, data = cohort
    value = attempt(data["documents"][0])
    p = value["provenance"]
    p["request"]["messages"][0]["content"][0]["text"] = "Different document"
    p["request_sha256"] = identity(p["request"])
    run = run_file(path, "wrong-text", {"sample": value})
    row = qualification_report(path, [run])["runs"][0]["documents"][0]
    assert "request prompt/text identity unverified" in row["provenance_issues"]


def test_parsed_response_cannot_disagree_with_retained_claude_output(cohort):
    path, data = cohort
    value = attempt(data['documents'][0])
    value['raw_response']['content'][0]['text'] = '{"contracts": []}'
    run = run_file(path, 'changed-response', {'sample': value})
    row = qualification_report(path, [run])['runs'][0]['documents'][0]
    assert 'parsed extraction differs from raw response' in row['provenance_issues']


def test_reusing_a_response_does_not_count_as_independent_repeats(cohort):
    path, data = cohort
    doc = data["documents"][0]
    doc["difficult"] = True
    path.write_text(json.dumps(data))
    sync, batch = attempt(doc), attempt(doc, mode="batch")
    runs = [run_file(path, str(i), {"sample": value}) for i, value in enumerate([sync, batch, sync, batch])]
    assert not qualification_report(path, runs)["corpora"]["ep"]["qualified"]


def test_malformed_responses_and_missing_text_are_not_empty_successes(cohort):
    path, data = cohort
    bad = attempt(data["documents"][0])
    bad["extraction"] = {"contracts": [{"bids": None}]}
    run = run_file(path, "bad", {"sample": bad})
    assert qualification_report(path, [run])["runs"][0]["documents"][0]["state"] == "malformed"
    (path.parent / "source.txt").unlink()
    assert qualification_report(path, [run])["runs"][0]["documents"][0]["state"] == "missing"


def test_changed_source_or_unbound_labels_refuse_evaluation(cohort):
    path, data = cohort
    data["documents"][0]["relations"][0]["evidence"] = ["not printed"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="literal source"):
        load_cohort(path)
    (path.parent / "source.txt").write_text("Changed source")
    with pytest.raises(ValueError, match="hash changed"):
        load_cohort(path)


def test_empty_negative_document_still_detects_fabricated_empty_contract(cohort):
    path, data = cohort
    doc = data["documents"][0]
    doc.update(contracts=[], relations=[])
    path.write_text(json.dumps(data))
    value = attempt(doc)
    value["extraction"] = {"contracts": [{"reference": "invented", "bids": [], "awards": []}]}
    run = run_file(path, "negative", {"sample": value})
    row = qualification_report(path, [run])["runs"][0]["documents"][0]
    assert row["metrics"]["correct"] == 0
    assert row["contract_extras"] == ["invented"] and not row["contracts_match"]


def test_independent_source_count_finds_shortfall_when_model_omits_declaration(cohort):
    path, data = cohort
    doc = data['documents'][0]
    source = path.parent / 'source.txt'
    source.write_text(source.read_text() + '\nThree submissions received.')
    doc['text_sha256'] = digest(source.read_bytes())
    doc['source_counts'] = [{'reference': '123', 'submissions': 3, 'evidence': ['Three submissions received.']}]
    path.write_text(json.dumps(data))
    run = run_file(path, 'missing-count', {'sample': attempt(doc)})
    row = qualification_report(path, [run])['runs'][0]['documents'][0]
    assert row['count_declaration_errors'] == [{'reference': '123', 'expected': 3, 'model_declaration': None}]
    assert row['count_discrepancies'][0]['declared'] == 3
    assert row['count_discrepancies'][0]['basis'] == 'independent_source'


def test_mode_agreement_is_reported_only_for_matching_known_requests(cohort):
    path, data = cohort
    doc = data['documents'][0]
    runs = [run_file(path, 'sync', {'sample': attempt(doc)}),
            run_file(path, 'batch', {'sample': attempt(doc, mode='batch')})]
    profile = qualification_report(path, runs)['corpora']['ep']['comparisons'][0]['profiles'][0]
    assert profile['sync_batch_exact_agreement'] is True
    old = attempt(doc)
    old['provenance']['request_sha256'] = None
    run = run_file(path, 'historical', {'sample': old})
    profile = qualification_report(path, [run])['corpora']['ep']['comparisons'][0]['profiles'][0]
    assert profile['sync_batch_exact_agreement'] is None


def test_only_explicit_source_reviewed_reference_aliases_are_normalized(cohort):
    path, data = cohort
    doc = data['documents'][0]
    source = path.parent / 'source.txt'
    source.write_text(source.read_text() + '\nRFQ 123')
    doc['text_sha256'] = digest(source.read_bytes())
    doc['reference_aliases'] = [{'alias': 'RFQ 123', 'canonical': '123', 'evidence': ['RFQ 123']}]
    path.write_text(json.dumps(data))
    value = attempt(doc)
    value['extraction']['contracts'][0]['reference'] = 'RFQ 123'
    run = run_file(path, 'prefix', {'sample': value})
    row = qualification_report(path, [run])['runs'][0]['documents'][0]
    assert row['metrics']['correct'] == 1 and row['contracts_match']
    assert row['reference_normalizations'] == [{'literal': 'RFQ 123', 'canonical': '123'}]
    value['extraction']['contracts'][0]['reference'] = 'RFQ 456'
    run = run_file(path, 'other-contract', {'sample': value})
    row = qualification_report(path, [run])['runs'][0]['documents'][0]
    assert len(row['metrics']['false_joins']) == 1 and not row['contracts_match']


def test_supplier_alias_is_document_bound_and_does_not_accept_another_name(cohort):
    path, data = cohort
    doc = data['documents'][0]
    source = path.parent / 'source.txt'
    source.write_text(source.read_text() + '\nSupplier Ltd. is also printed as Vendor.')
    doc['text_sha256'] = digest(source.read_bytes())
    doc['supplier_aliases'] = [{'alias': 'Vendor', 'canonical': 'Supplier Ltd.',
                              'evidence': ['Supplier Ltd. is also printed as Vendor.']}]
    path.write_text(json.dumps(data))
    value = attempt(doc)
    value['extraction']['contracts'][0]['bids'][0]['supplier_name'] = 'Vendor'
    run = run_file(path, 'alternate-spelling', {'sample': value})
    row = qualification_report(path, [run])['runs'][0]['documents'][0]
    assert row['metrics']['correct'] == 1
    assert row['supplier_normalizations'] == [{'literal': 'Vendor', 'canonical': 'Supplier Ltd.'}]
    value['extraction']['contracts'][0]['bids'][0]['supplier_name'] = 'Another Vendor'
    run = run_file(path, 'unreviewed-name', {'sample': value})
    row = qualification_report(path, [run])['runs'][0]['documents'][0]
    assert len(row['metrics']['fabrications']) == 1


def test_source_aliases_apply_to_label_spellings_as_well_as_observations(cohort):
    path, data = cohort
    doc = data['documents'][0]
    source = path.parent / 'source.txt'
    source.write_text(source.read_text() + '\nSupplier Ltd. is also printed as Vendor.')
    doc['text_sha256'] = digest(source.read_bytes())
    doc['supplier_aliases'] = [{'alias': 'Vendor', 'canonical': 'Supplier Ltd.',
                              'evidence': ['Supplier Ltd. is also printed as Vendor.']}]
    doc['relations'].append({**relation(supplier='Vendor'), 'evidence': ['Vendor']})
    path.write_text(json.dumps(data))
    value = attempt(doc)
    value['extraction']['contracts'][0]['bids'].append(copy.deepcopy(value['extraction']['contracts'][0]['bids'][0]))
    run = run_file(path, 'same-source-firm', {'sample': value})
    row = qualification_report(path, [run])['runs'][0]['documents'][0]
    assert row['metrics']['correct'] == 2
    assert doc['relations'][1]['supplier'] == 'Vendor'  # Original labels survive.


def test_runtime_shortfalls_have_source_bound_reviews_without_changing_facts(conn):
    value = extraction()
    value["contracts"][0]["declared_submissions"] = 4
    conn.execute("INSERT INTO background_pdf(url,sha256,text) VALUES ('https://source','hash','Only the winner is public.')")
    mark_extracted(conn, "hash", EXTRACTOR_VERSION, result_json=json.dumps(value))
    before = conn.iterdump()
    before = list(before)
    report = count_discrepancy_report(conn)
    assert report["unresolved"] == 1
    key = report["findings"][0]["review_key"]
    reviews = {key: {"disposition": "unpublished_bidders", "reason": "Roster is not public.",
                     "evidence": ["Only the winner is public."]}}
    assert count_discrepancy_report(conn, reviews=reviews)["unresolved"] == 0
    assert list(conn.iterdump()) == before
    reviews[key]["evidence"] = ["Not in source"]
    assert count_discrepancy_report(conn, reviews=reviews)["unresolved"] == 1
    reviews[key]["evidence"] = ["Only the winner is public."]
    changed = copy.deepcopy(value)
    changed["contracts"][0]["declared_submissions"] = 5
    mark_extracted(conn, "hash", EXTRACTOR_VERSION, result_json=json.dumps(changed))
    assert count_discrepancy_report(conn, reviews=reviews)["unresolved"] == 1


def test_packaged_cohort_loads_and_report_is_reproducible():
    root = Path(__file__).resolve().parents[2] / "docs/ground-truth/extraction-qualification"
    cohort = load_cohort(root / "cohort.json")
    assert {d["corpus"] for d in cohort["documents"]} == REQUIRED_CORPORA
    runs = sorted((root / "runs").glob("*.json"))
    assert qualification_report(root / "cohort.json", runs) == json.loads((root / "report.json").read_text())


def test_real_runtime_subset_dispositions_are_count_only_and_read_only(conn):
    root = Path(__file__).resolve().parents[2] / 'docs/ground-truth/extraction-qualification'
    cohort = load_cohort(root / 'cohort.json')
    docs = {d['document_sha256']: d for d in cohort['documents']}
    proofs = json.loads((root / 'runtime-count-review-evidence.json').read_text())
    reviews = json.loads((root / 'runtime-count-reviews.json').read_text())
    for proof in proofs:
        finding = proof['finding']
        doc = docs[finding['document_sha256']]
        conn.execute('INSERT OR IGNORE INTO background_pdf(url,sha256,text) VALUES (?,?,?)',
                     (finding['url'], finding['document_sha256'], (root / doc['text_path']).read_text()))
        mark_extracted(conn, finding['document_sha256'], finding['prompt_version'],
                       result_json=json.dumps(proof['extraction']))
    before = list(conn.iterdump())
    result = count_discrepancy_report(conn, reviews=reviews)
    assert len(result['findings']) == 5 and result['unresolved'] == 1
    resolved = [f for f in result['findings'] if f['resolved']]
    assert len(resolved) == 4
    assert all(f['review']['other_material_issues'] for f in resolved)
    assert list(conn.iterdump()) == before


def test_cli_reports_incomplete_evidence_without_opening_database(cohort, monkeypatch):
    from toronto_bids import cli

    path, data = cohort
    monkeypatch.setattr(cli, "_open_db", lambda: pytest.fail("Qualification must not open operational DB"))
    run = run_file(path, "pending", {"sample": {"state": "pending"}})
    out = path.parent / "report.json"
    assert cli.main(["qualify-extraction", "--cohort", str(path), "--run", str(run), "--out", str(out)]) == 1
    assert json.loads(out.read_text())["corpora"]["ep"]["states"] == {"pending": 1}


def test_count_cli_does_not_create_a_missing_database(tmp_path, monkeypatch, capsys):
    from toronto_bids import cli

    missing = tmp_path / "missing.sqlite"
    monkeypatch.setattr(cli.config, "DB_PATH", missing)
    assert cli.main(["extraction-counts"]) == 1
    assert not missing.exists()
    assert "unable to open" in capsys.readouterr().err
