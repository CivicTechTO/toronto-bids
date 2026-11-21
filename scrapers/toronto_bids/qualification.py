"""Offline, source-bound extraction qualification (#302).

Inputs are reviewed labels and immutable trial artifacts, never derived store rows.
No extraction client, operational database or paid request is opened by this module.
"""

from collections import Counter
import hashlib
import json
from pathlib import Path

from toronto_bids.extract import validate_extraction
from toronto_bids.extraction import CORPORA, check_declared_counts


FIELDS = ("supplier", "contract", "stage", "role", "amount", "basis", "status")
STATES = {"success", "pending", "failed", "skipped", "missing"}
REQUIRED_CORPORA = set(CORPORA) | {"waterfront"}


class QualificationEvidenceError(ValueError):
    """The production evidence gate requires review; the model JSON is not malformed."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identity(value):
    """Ordered JSON identity: schema property order is part of the request."""
    return digest(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def request_prompt_prefix(provenance):
    """Verify an original or explicitly isolated, hash-bound experimental prompt."""
    from toronto_bids.extract import EXTRACTOR_VERSION, build_prompt

    original = build_prompt('')
    experiment = provenance.get('experimental_prompt')
    if experiment is None:
        if provenance.get('prompt_version') != EXTRACTOR_VERSION:
            raise ValueError('Unverified production prompt version')
        return original
    if not isinstance(experiment, dict) or not isinstance(experiment.get('prefix'), str):
        raise ValueError('Malformed experimental prompt profile')
    prefix = experiment['prefix']
    marker = 'DOCUMENT TEXT:\n\n'
    sha = digest(prefix.encode())
    if (experiment.get('base_version') != EXTRACTOR_VERSION or experiment.get('sha256') != sha
            or provenance.get('prompt_version') != 'eval-' + sha[:12]
            or not prefix.startswith(original.removesuffix(marker)) or not prefix.endswith(marker)):
        raise ValueError('Experimental prompt identity changed')
    return prefix


def _text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Relationship fields must be strings or null")
    return " ".join(value.split())


def _key(relation):
    values = [_text(relation[f]) for f in FIELDS]
    # Typography and case do not change a supplier; legal suffixes remain significant.
    if not values[0]:
        raise ValueError("Relationship missing supplier")
    values[0] = values[0].replace("’", "'").casefold()
    return tuple(values)


def relationships(extraction):
    """Keep multiplicity, stage and role; do not merge repeated supplier claims."""
    validate_extraction(extraction)
    result = []
    for contract in extraction["contracts"]:
        reference, stage = contract.get("reference"), contract.get("stage")
        for field, role in (("bids", "bid"), ("awards", "award"),
                            ("prequalified", "prequalified"),
                            ("invited_to_tender", "invited_to_tender")):
            entries = contract.get(field, [])
            if not isinstance(entries, list):
                raise ValueError(f"{field} must be a list")
            for entry in entries:
                if role in {"bid", "award"}:
                    if not isinstance(entry, dict):
                        raise ValueError(f"{field} entry must be an object")
                    supplier = entry["supplier_name"]
                    amount, basis = entry.get("amount_raw"), entry.get("amount_basis")
                    status = entry.get("status") if role == "bid" else None
                else:
                    supplier, amount, basis, status = entry, None, None, None
                relation = dict(zip(FIELDS, (supplier, reference, stage, role, amount, basis, status)))
                _key(relation)
                result.append(relation)
    return result


def waterfront_relationships(doc, extraction, base):
    """Score the actual publication path, retaining every refused raw award."""
    from toronto_bids.sources.waterfront import parse_publication, verified_claims

    validate_extraction(extraction)
    rows = [r for r in parse_publication((base / doc['publication_path']).read_bytes())
            if r['publication_month'] == doc['publication_month']]
    observations = []
    try:
        _, errors = verified_claims(rows, extraction, observations=observations)
    except ValueError as exc:
        raise QualificationEvidenceError(str(exc)) from exc
    relations = []
    for observation in observations:
        contract = extraction['contracts'][observation['contract_index']]
        award = contract['awards'][observation['award_index']]
        if observation['disposition'] in {'admitted', 'duplicate'}:
            supplier, reference, amount = (observation[k] for k in ('supplier', 'publication_key', 'amount'))
        else:
            supplier, reference, amount = award['supplier_name'], contract.get('reference'), award.get('amount_raw')
        # The publication path stores supplier/value/source-block evidence, not a
        # model's invented reference, tax basis or tender status.
        relations.append(dict(zip(FIELDS, (supplier, reference, 'single_stage', 'award', amount, None, None))))
    unsupported = [r for r in relationships(extraction) if r['role'] != 'award']
    relations.extend(unsupported)
    return relations, errors


def score(expected, actual):
    """Exact full-relation metrics, with disjoint categories for surplus claims."""
    truth = Counter(_key(r) for r in expected)
    observed = Counter(_key(r) for r in actual)
    missing, extra = truth - observed, observed - truth
    errors = {k: [] for k in ("false_joins", "attribute_errors", "fabrications", "duplicates")}
    for key, count in extra.items():
        if key in truth:
            category = "duplicates"
        elif any(key[:4] == other[:4] for other in truth):
            category = "attribute_errors"
        elif any(key[0] == other[0] for other in truth):
            category = "false_joins"
        else:
            category = "fabrications"
        errors[category].extend([dict(zip(FIELDS, key))] * count)
    tp = sum((truth & observed).values())
    return {
        "expected": sum(truth.values()), "observed": sum(observed.values()),
        "correct": tp, "precision": tp / sum(observed.values()) if observed else 1.0,
        "recall": tp / sum(truth.values()) if truth else 1.0,
        "omissions": [dict(zip(FIELDS, k)) for k, n in missing.items() for _ in range(n)],
        **errors,
    }


def _amount_values(relations):
    """Supplement exact raw comparisons with the archive's existing strict parsers.

    No new money parser or relaxed qualification threshold: rates, foreign money
    and malformed values keep their literals. Bid-table footnotes follow the
    same established parse_bid_price rule as normal archive ingestion.
    """
    from toronto_bids.amount import parse_amount, parse_bid_price

    result = []
    for relation in relations:
        parser = parse_bid_price if relation['role'] == 'bid' else parse_amount
        numeric = parser(relation['amount'])
        amount = repr(numeric) if numeric is not None else relation['amount']
        result.append({**relation, 'amount': amount})
    return result


def load_cohort(path):
    path = Path(path)
    cohort = json.loads(path.read_text())
    if cohort.get("version") != 1:
        raise ValueError("Unsupported qualification cohort version")
    ids = [d["id"] for d in cohort["documents"]]
    if len(ids) != len(set(ids)) or not ids:
        raise ValueError("Cohort document IDs must be unique and nonempty")
    for name, threshold in cohort["thresholds"].items():
        if name not in REQUIRED_CORPORA or threshold != {
            "precision": 1.0, "recall": 1.0, "modes": ["sync", "batch"], "difficult_repeats": 2,
        }:
            raise ValueError("Qualification requires exact relations, both modes and difficult repeats")
    for doc in cohort["documents"]:
        if doc["corpus"] not in cohort["thresholds"]:
            raise ValueError(f"Missing predeclared threshold: {doc['corpus']}")
        # Missing files stay in the denominator; changed held bytes invalidate the review.
        source = path.parent / doc["text_path"]
        if source.exists() and digest(source.read_bytes()) != doc["text_sha256"]:
            raise ValueError(f"Source text hash changed: {doc['id']}")
        if doc['corpus'] == 'waterfront' and doc.get('publication_path'):
            from toronto_bids.sources.waterfront import parse_publication, extraction_text

            publication = (path.parent / doc['publication_path']).read_bytes()
            if digest(publication) != doc['document_sha256']:
                raise ValueError(f"Publication hash changed: {doc['id']}")
            rows = [r for r in parse_publication(publication)
                    if r['publication_month'] == doc['publication_month']]
            if source.exists() and extraction_text(doc['publication_month'], rows) != source.read_text():
                raise ValueError(f"Publication/input text mismatch: {doc['id']}")
        if doc["review_status"] == "reviewed":
            if not source.exists():
                continue
            evidence_text = source.read_text()
            for supplement in doc.get('additional_evidence', []):
                held = (path.parent / supplement['text_path']).read_bytes()
                if digest(held) != supplement['text_sha256']:
                    raise ValueError(f"Supplementary evidence hash changed: {doc['id']}")
                if supplement.get('pdf_path') and digest((path.parent / supplement['pdf_path']).read_bytes()) != supplement['held_pdf_sha256']:
                    raise ValueError(f"Supplementary PDF hash changed: {doc['id']}")
                evidence_text += '\n' + held.decode('utf-8')
            text = _text(evidence_text)
            for relation in doc["relations"]:
                _key(relation)
                if not relation.get("evidence") or any(_text(q) not in text for q in relation["evidence"]):
                    raise ValueError(f"Missing literal source evidence: {doc['id']}")
            for count in doc.get('source_counts', []):
                if (count['reference'] not in doc['contracts'] or type(count['submissions']) is not int
                        or count['submissions'] < 0 or not count.get('evidence')
                        or any(_text(q) not in text for q in count['evidence'])):
                    raise ValueError(f"Invalid independent count evidence: {doc['id']}")
            aliases = doc.get('reference_aliases', [])
            if len({a['alias'] for a in aliases}) != len(aliases):
                raise ValueError(f"Ambiguous reviewed reference alias: {doc['id']}")
            for alias in aliases:
                if (alias['canonical'] not in doc['contracts'] or not alias.get('evidence')
                        or any(_text(q) not in text for q in alias['evidence'])):
                    raise ValueError(f"Unbound reference alias: {doc['id']}")
            supplier_aliases = doc.get('supplier_aliases', [])
            if len({_text(a['alias']).casefold() for a in supplier_aliases}) != len(supplier_aliases):
                raise ValueError(f"Ambiguous reviewed supplier alias: {doc['id']}")
            for alias in supplier_aliases:
                if (alias['canonical'] not in {r['supplier'] for r in doc['relations']}
                        or not alias.get('evidence') or any(_text(q) not in text for q in alias['evidence'])):
                    raise ValueError(f"Unbound supplier alias: {doc['id']}")
            if not doc.get("review_notes"):
                raise ValueError(f"Missing review notes: {doc['id']}")
        elif doc["review_status"] != "pending":
            raise ValueError("Invalid review status")
    return cohort


def _provenance(doc, attempt, extraction, text):
    """Unknown historical request identities remain unknown, never reconstructed as proof."""
    p = attempt.get("provenance", {})
    required = ("document_sha256", "text_sha256", "prompt_version", "model", "effort",
                "schema_sha256", "request_sha256", "mode", "response_sha256", "response_id")
    issues = [f"missing {field}" for field in required if not p.get(field)]
    if p.get("text_sha256") != doc["text_sha256"]:
        issues.append("text identity mismatch")
    if doc.get("document_sha256") and p.get("document_sha256") != doc["document_sha256"]:
        issues.append("document identity mismatch")
    if p.get("mode") not in {"sync", "batch"}:
        issues.append("unknown execution mode")
    if p.get("response_sha256") != identity(extraction):
        issues.append("response identity mismatch")
    if p.get("request"):
        request = p["request"]
        if identity(request) != p.get("request_sha256"):
            issues.append("request identity mismatch")
        if identity(request.get("output_config", {}).get("format", {}).get("schema")) != p.get("schema_sha256"):
            issues.append("schema identity mismatch")
        if request.get("model") != p.get("model") or request.get("output_config", {}).get("effort") != p.get("effort"):
            issues.append("request configuration mismatch")
        messages = request.get("messages", [])
        content = messages[0].get("content", []) if len(messages) == 1 else []
        try:
            expected_prompt = request_prompt_prefix(p) + text
        except ValueError:
            expected_prompt = None
        if (len(messages) != 1 or messages[0].get("role") != "user"
                or not isinstance(content, list)
                or any(b.get("type") != "text" for b in content)
                or "".join(b.get("text", "") for b in content) != expected_prompt):
            issues.append("request prompt/text identity unverified")
    else:
        issues.append("exact request not retained")
    usage = attempt.get("usage") or {}
    if any(type(usage.get(k)) is not int or usage[k] < 0 for k in ("input_tokens", "output_tokens")):
        issues.append("measured usage missing or invalid")
    if type(attempt.get("charged_microdollars")) is not int or attempt["charged_microdollars"] < 0:
        issues.append("measured charge missing or invalid")
    raw = attempt.get('raw_response')
    if not isinstance(raw, dict):
        issues.append('raw Claude response not retained')
    else:
        from toronto_bids.claude import _charge, parse_response

        if raw.get('id') != p.get('response_id') or raw.get('model') != p.get('model'):
            issues.append('raw response identity/model mismatch')
        if raw.get('usage') != attempt.get('usage'):
            issues.append('raw usage mismatch')
        try:
            if parse_response(raw) != extraction:
                issues.append('parsed extraction differs from raw response')
            charge = _charge(p.get('model'), raw.get('usage'))
            if p.get('mode') == 'batch':
                charge = (charge + 1) // 2
            if charge != attempt.get('charged_microdollars'):
                issues.append('reported charge differs from reviewed usage rates')
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(f'raw response/usage refused: {exc}')
    return issues


def _evaluate(doc, attempt, base):
    row = {"document": doc["id"], "corpus": doc["corpus"],
           "scope_complete": doc.get('scope_complete', True),
           "input_scope": doc.get('input_scope', 'selected document'),
           "state": attempt.get("state", "missing"), "reason": attempt.get("reason"),
           'dispatch': attempt.get('dispatch'),
           'experimental_prompt': bool(attempt.get('provenance', {}).get('experimental_prompt')),
           "provenance": attempt.get("provenance", {}), "usage": attempt.get("usage"),
           "charged_microdollars": attempt.get("charged_microdollars")}
    if row["state"] not in STATES:
        raise ValueError(f"Unknown attempt state: {row['state']}")
    if not (base / doc["text_path"]).exists():
        row.update(state="missing", reason="source text missing")
    if row["state"] != "success":
        return row
    if doc["review_status"] != "reviewed":
        row.update(state="unreviewed", reason="independent source review pending")
        return row
    try:
        extraction = attempt["extraction"]
        row["provenance_issues"] = _provenance(doc, attempt, extraction, (base / doc["text_path"]).read_text())
        source_errors = []
        if doc['corpus'] == 'waterfront':
            actual, source_errors = waterfront_relationships(doc, extraction, base)
        else:
            actual = relationships(extraction)
        aliases = {_text(a['alias']): a['canonical'] for a in doc.get('reference_aliases', [])}
        supplier_aliases = {_text(a['alias']).casefold(): a['canonical'] for a in doc.get('supplier_aliases', [])}
        row['reference_normalizations'] = []
        row['supplier_normalizations'] = []
        for relation in actual:
            literal = _text(relation['contract'])
            if literal in aliases:
                row['reference_normalizations'].append({'literal': relation['contract'], 'canonical': aliases[literal]})
                relation['contract'] = aliases[literal]
            supplier = _text(relation['supplier']).casefold()
            if supplier in supplier_aliases:
                row['supplier_normalizations'].append({'literal': relation['supplier'],
                                                       'canonical': supplier_aliases[supplier]})
                relation['supplier'] = supplier_aliases[supplier]
        expected_relations = []
        row['label_supplier_normalizations'] = []
        for labelled in doc['relations']:
            expected = dict(labelled)
            supplier = _text(expected['supplier']).casefold()
            if supplier in supplier_aliases:
                row['label_supplier_normalizations'].append({'literal': expected['supplier'],
                                                             'canonical': supplier_aliases[supplier]})
                expected['supplier'] = supplier_aliases[supplier]
            expected_relations.append(expected)
        row['source_errors'] = source_errors
        row["metrics"] = score(expected_relations, actual)
        row['amount_value_metrics'] = score(_amount_values(expected_relations), _amount_values(actual))
        row["response_identity"] = identity(sorted((_key(r) for r in actual), key=repr))
        expected_refs = Counter(_text(r) for r in doc["contracts"])
        actual_refs = (Counter(list(dict.fromkeys(r['contract'] for r in actual))) if doc['corpus'] == 'waterfront'
                       else Counter(aliases.get(_text(c.get('reference')), _text(c.get('reference')))
                                    for c in extraction['contracts']))
        row["contracts_match"] = expected_refs == actual_refs
        row["contract_omissions"] = list((expected_refs - actual_refs).elements())
        row["contract_extras"] = list((actual_refs - expected_refs).elements())
        row["count_discrepancies"] = []
        flags = check_declared_counts(extraction)
        row['source_count_checks'] = []
        row['count_declaration_errors'] = []
        for source_count in doc.get('source_counts', []):
            reference, expected = source_count['reference'], source_count['submissions']
            contracts = [c for c in extraction['contracts']
                         if aliases.get(_text(c.get('reference')), _text(c.get('reference'))) == reference]
            if len(contracts) != 1:
                row['source_count_checks'].append({'reference': reference, 'expected': expected,
                                                 'state': 'contract_missing_or_ambiguous'})
                continue
            contract = contracts[0]
            actual = len(contract.get('bids', []))
            declaration = contract.get('declared_submissions')
            row['source_count_checks'].append({'reference': reference, 'expected': expected,
                                             'actual': actual, 'model_declaration': declaration, 'state': 'checked'})
            if declaration != expected:
                row['count_declaration_errors'].append({'reference': reference,
                                                       'expected': expected, 'model_declaration': declaration})
            flag = {'reference': reference, 'declared': expected, 'actual': actual, 'delta': actual - expected}
            already_flagged = any(aliases.get(_text(f['reference']), _text(f['reference'])) == reference
                                  and f['declared'] == expected and f['actual'] == actual for f in flags)
            if actual < expected and not already_flagged:
                flags.append({**flag, 'basis': 'independent_source'})
        for flag in flags:
            # Reviews bind the source, response, reference and exact counts. A changed
            # response cannot silently inherit a previous subset justification.
            key = identity({"text_sha256": doc["text_sha256"],
                            "response_sha256": identity(extraction), "flag": flag})
            review = doc.get("count_reviews", {}).get(key)
            resolved = count_review_resolved(review, (base / doc["text_path"]).read_text())
            row["count_discrepancies"].append({**flag, "review_key": key,
                                               "review": review, "resolved": resolved})
    except QualificationEvidenceError as exc:
        row.update(state='source_review_required', reason=str(exc))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        row.update(state="malformed", reason=str(exc))
    return row


def qualification_report(cohort_path, run_paths):
    cohort_path = Path(cohort_path)
    cohort = load_cohort(cohort_path)
    docs = {d["id"]: d for d in cohort["documents"]}
    runs, run_ids = [], set()
    for path in run_paths:
        raw = Path(path).read_bytes()
        run = json.loads(raw)
        if run["id"] in run_ids:
            raise ValueError("Run IDs must be unique; repeats require separate runs")
        run_ids.add(run["id"])
        attempts = run["attempts"]
        if set(attempts) - docs.keys():
            raise ValueError("Run contains documents outside the selected cohort")
        membership = run.get('selected_documents', list(docs))
        if set(membership) - docs.keys() or set(attempts) - set(membership):
            raise ValueError('Invalid predeclared trial membership')
        rows = []
        for doc in docs.values():
            if doc['id'] in membership:
                rows.append(_evaluate(doc, attempts.get(doc['id'], {'state': 'missing', 'reason': 'not in run'}),
                                      cohort_path.parent))
            else:
                rows.append({'document': doc['id'], 'corpus': doc['corpus'], 'state': 'not_selected',
                             'reason': 'outside predeclared trial membership', 'charged_microdollars': None})
        runs.append({"id": run["id"], "artifact_sha256": digest(raw), "documents": rows})
    corpora = {}
    for corpus in sorted(REQUIRED_CORPORA):
        selected = [d for d in docs.values() if d["corpus"] == corpus]
        rows = [r for run in runs for r in run["documents"] if r["corpus"] == corpus]
        metrics = [r["metrics"] for r in rows if "metrics" in r and r["state"] == "success"]
        counts = {k: sum(m[k] for m in metrics) for k in ("expected", "observed", "correct")}
        counts.update({k: sum(len(m[k]) for m in metrics) for k in
                       ("omissions", "false_joins", "attribute_errors", "fabrications", "duplicates")})
        counts["precision"] = (counts["correct"] / counts["observed"] if counts["observed"]
                               else 1.0 if metrics else None)
        counts["recall"] = (counts["correct"] / counts["expected"] if counts["expected"]
                            else 1.0 if metrics else None)
        qualified = bool(selected and runs and all(d.get('scope_complete', True) for d in selected))
        comparisons = []
        for doc in selected:
            observations = [r for r in rows if r["document"] == doc["id"]]
            complete = [r for r in observations if r['state'] in ('success', 'source_review_required')
                        and not r.get('provenance_issues')]
            groups = {}
            for r in complete:
                p = r["provenance"]
                config = tuple(p[k] for k in ("prompt_version", "model", "effort", "schema_sha256", "request_sha256"))
                group = groups.setdefault(config, {"sync": set(), "batch": set()})
                group[p["mode"]].add(p["response_id"])
            modes = Counter(r["provenance"]["mode"] for r in complete)
            repeats = 2 if doc.get("difficult") else 1
            coverage = any(all(len(g[m]) >= repeats for m in ("sync", "batch"))
                           and not (g["sync"] & g["batch"]) for g in groups.values())
            signatures = {r["response_identity"] for r in observations if r["state"] == "success"}
            profiles = {}
            for r in observations:
                if r['state'] not in ('success', 'source_review_required'):
                    continue
                p = r['provenance']
                fields = ('prompt_version', 'model', 'effort', 'schema_sha256', 'request_sha256')
                key = tuple(p.get(k) for k in fields)
                profile = profiles.setdefault(key, {'configuration': dict(zip(fields, key)),
                                                     'sync': [], 'batch': []})
                if p.get('mode') in ('sync', 'batch'):
                    profile[p['mode']].append(r)
            comparisons_by_profile = []
            for profile in profiles.values():
                modes_summary = {}
                mode_variants = {}
                for mode in ('sync', 'batch'):
                    samples = profile[mode]
                    variants = {r['response_identity'] for r in samples if 'response_identity' in r}
                    mode_variants[mode] = variants
                    modes_summary[mode] = {'observations': len(samples),
                                           'distinct_response_ids': len({r['provenance'].get('response_id')
                                                                        for r in samples if r['provenance'].get('response_id')}),
                                           'raw_extraction_variants': len({r['provenance'].get('response_sha256')
                                                                         for r in samples if r['provenance'].get('response_sha256')}),
                                           'relationship_variants': len(variants)}
                known = all(profile['configuration'].values())
                paired = known and all(mode_variants.values())
                comparisons_by_profile.append({'configuration': profile['configuration'], 'modes': modes_summary,
                                               'request_identity_known': known,
                                               'sync_batch_exact_agreement': (
                                                   len(mode_variants['sync']) == len(mode_variants['batch']) == 1
                                                   and mode_variants['sync'] == mode_variants['batch']) if paired else None})
            comparisons.append({"document": doc["id"], "proven_modes": dict(modes),
                                "required_per_mode": repeats, "covered": coverage,
                                'relationship_evaluation_complete': bool(observations) and all(
                                    r['state'] in ('success', 'not_selected') for r in observations),
                                "distinct_relationship_outputs": len(signatures),
                                'profiles': comparisons_by_profile})
            qualified &= coverage
        # Every retained failed/incomplete trial stays visible and prevents a blanket pass.
        for row in rows:
            if row['state'] == 'not_selected':
                continue
            m = row.get("metrics", {})
            qualified &= (row["state"] == "success" and not row.get("provenance_issues")
                          and not row.get('experimental_prompt')
                          and row.get("contracts_match", False)
                          and m.get("precision") == 1 and m.get("recall") == 1
                          and not row.get('source_errors')
                          and not row.get('count_declaration_errors')
                          and not any(not f["resolved"] for f in row.get("count_discrepancies", [])))
        unresolved = [a for d in selected for a in d.get("adjudications", []) if a["status"] == "unresolved"]
        qualified &= not unresolved
        corpora[corpus] = {"selected": len(selected),
                           "reviewed_selected": sum(d["review_status"] == "reviewed" for d in selected),
                           "scored_document_attempts": len(metrics),
                           "selected_reviewed_relationships": sum(len(d["relations"]) for d in selected
                                                                  if d["review_status"] == "reviewed"),
                           "states": dict(Counter(r["state"] for r in rows)),
                           "metrics": counts, "comparisons": comparisons,
                           "unresolved_adjudications": unresolved, "qualified": bool(qualified)}
        value_metrics = [r['amount_value_metrics'] for r in rows if r['state'] == 'success']
        corpora[corpus]['amount_value_matches'] = {
            'expected': sum(m['expected'] for m in value_metrics),
            'observed': sum(m['observed'] for m in value_metrics),
            'correct': sum(m['correct'] for m in value_metrics),
        }
    all_rows = [r for run in runs for r in run['documents']]
    charges = [r['charged_microdollars'] for r in all_rows
               if type(r['charged_microdollars']) is int and r['charged_microdollars'] >= 0]
    from importlib import resources

    review_bytes = resources.files('toronto_bids.data').joinpath('publication_award_evidence.json').read_bytes()
    return {"version": 1, "cohort_sha256": digest(cohort_path.read_bytes()),
            'publication_recipient_registry_sha256': digest(review_bytes),
            "thresholds": cohort["thresholds"], "selected": len(docs), "runs": runs,
            "corpora": corpora, "qualified": all(c["qualified"] for c in corpora.values()),
            "usage": {"known_charged_microdollars": sum(charges),
                      'unattempted_inputs': sum(r['state'] in ('missing', 'not_selected') for r in all_rows),
                      'prepared_not_sent': sum(r.get('dispatch') == 'prepared' for r in all_rows),
                      "attempts_without_charge": sum(r['charged_microdollars'] is None
                          and r['state'] not in ('missing', 'not_selected')
                          and r.get('dispatch') != 'prepared' for r in all_rows)}}


def count_review_resolved(review, text):
    return bool(isinstance(review, dict) and isinstance(review.get("reason"), str) and review["reason"].strip()
                and isinstance(review.get("evidence"), list) and review["evidence"]
                and review.get("disposition") in {"published_subset", "unpublished_bidders"}
                and all(isinstance(q, str) and q.strip() and _text(q) in _text(text)
                        for q in review["evidence"]))


def count_discrepancy_report(conn, *, reviews=None):
    """Read cached runtime shortfalls, binding every review to unchanged evidence.

    Does not reject extraction or change admitted facts. An unresolved result is a
    review queue, including warnings written by both synchronous and batch paths.
    """
    from toronto_bids.extract import EXTRACTOR_VERSION

    if reviews is not None and not isinstance(reviews, dict):
        raise ValueError("Count reviews must be an object keyed by review identity")
    reviews = reviews or {}
    rows = conn.execute(
        "SELECT bp.url,bp.sha256,bp.text,ec.result_json FROM background_pdf bp "
        "JOIN extraction_cache ec ON ec.sha256=bp.sha256 "
        "WHERE ec.extractor_version=? ORDER BY bp.url", (EXTRACTOR_VERSION,)
    ).fetchall()
    findings, malformed = [], []
    for row in rows:
        url, sha256, text, raw = row
        try:
            result = validate_extraction(json.loads(raw))
            for flag in check_declared_counts(result):
                key = identity({"document_sha256": sha256, "prompt_version": EXTRACTOR_VERSION,
                                "response_sha256": identity(result), "flag": flag})
                review = reviews.get(key)
                findings.append({"url": url, "document_sha256": sha256,
                                 "prompt_version": EXTRACTOR_VERSION, **flag,
                                 "review_key": key, "review": review,
                                 "resolved": count_review_resolved(review, text or "")})
        except (ValueError, TypeError, KeyError) as exc:
            malformed.append({"url": url, "reason": str(exc)})
    return {"reviewed_cache_documents": len(rows), "findings": findings, "malformed": malformed,
            "unused_review_keys": sorted(set(reviews) - {f["review_key"] for f in findings}),
            "unresolved": sum(not f["resolved"] for f in findings)}
