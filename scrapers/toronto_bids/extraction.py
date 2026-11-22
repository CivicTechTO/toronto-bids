"""Orchestrate corpus extraction, classification gating, caching, and store backfill."""

import json
import hashlib
from pathlib import Path

import httpx

from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.batch_extraction import ExtractionPending
from toronto_bids.store.db import get_extraction, is_extracted, mark_extracted

CORPORA = {
    "ttc": "kind='ttc_board'",
    "trca": "kind='agency_board' AND url LIKE '%escribemeetings%'",
    "ep": "kind='agency_board' AND url LIKE '%/ep/%'",
    "zoo": "kind='agency_board' AND url LIKE '%/zb/%'",
    "award_summary": "kind='award_summary'",
    "committee": "kind='committee_award'",
    # 2009-2012 Bid Committee composite reports (keyed on Call Number).
    "composite": "kind='bgrd' AND substr(reference,1,4) BETWEEN '2009' AND '2012'",
    # Bid Award Panel staff reports (bids only; awards are on OData).
    "ba_report": "kind='bgrd' AND reference LIKE '%.BA%'",
    "bd_report": "kind='bgrd' AND reference LIKE '%.BD%' AND substr(reference,1,4) BETWEEN '2013' AND '2016'",
}


def load_classification_labels(path) -> dict[str, bool]:
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {
        entry["url"]: entry["contains_bid_or_award"] for entry in data.get("labels", [])
    }


_DEFAULT_MAX_CHARS = 400_000


def split_document(text: str, max_chars: int = _DEFAULT_MAX_CHARS) -> list[str]:
    """Split a large document into chunks on double-newline boundaries."""
    if len(text) <= max_chars:
        return [text]
    sections = text.split("\n\n")
    chunks = []
    current = []
    current_len = 0
    for section in sections:
        section_len = len(section) + (2 if current else 0)
        if current and current_len + section_len > max_chars:
            chunks.append("\n\n".join(current))
            current = [section]
            current_len = len(section)
        else:
            current.append(section)
            current_len += section_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def dedup_contracts(contracts: list[dict]) -> list[dict]:
    """Deduplicate contracts by reference, keeping the one with more bids."""
    by_ref: dict[str, dict] = {}
    no_ref = []
    for contract in contracts:
        ref = contract.get("reference")
        if not ref:
            no_ref.append(contract)
            continue
        existing = by_ref.get(ref)
        if existing is None or len(contract.get("bids", [])) > len(
            existing.get("bids", [])
        ):
            by_ref[ref] = contract
    return list(by_ref.values()) + no_ref


# Corpora with non-procurement documents that the offline classifier may skip when unlabelled.
_CLASSIFIER_GATED_CORPORA = frozenset({"trca", "ep", "zoo", "composite"})

_DEFAULT_CLASSIFIER = object()


def _resolve_classifier(corpus, classifier):
    """Resolve classifier for corpus (default model for gated corpora, None to disable)."""
    if classifier is not _DEFAULT_CLASSIFIER:
        return classifier
    if corpus not in _CLASSIFIER_GATED_CORPORA:
        return None
    from toronto_bids.classify import default_classifier

    return default_classifier()


def _skip_reason(text, url, labels, classifier=None) -> str | None:
    """Return skip reason for a document, or None if eligible for extraction.

    Shared by extract_corpus and _count_uncached so cache counts stay aligned. Explicit labels override classifier.
    """
    if not text or not text.strip():
        return "no_text"
    if url in labels:
        return None if labels[url] else "skipped_classification"
    if classifier is not None and classifier.is_confidently_non_procurement(text):
        return "skipped_classifier"
    return None


def extract_corpus(
    conn,
    corpus,
    *,
    client,
    labels,
    where=None,
    limit=None,
    max_chars=_DEFAULT_MAX_CHARS,
    log=lambda _m: None,
    classifier=_DEFAULT_CLASSIFIER,
):
    """Extract bids from qualifying uncached documents in a corpus and return summary stats."""
    classifier = _resolve_classifier(corpus, classifier)
    sql_where = where or CORPORA.get(corpus)
    if sql_where is None:
        raise ValueError(
            f"Unknown corpus {corpus!r}, expected one of: {', '.join(sorted(CORPORA))}"
        )
    query = (
        f"SELECT * FROM background_pdf "
        f"WHERE {sql_where} AND sha256 IS NOT NULL "
        f"ORDER BY url"
    )
    rows = conn.execute(query).fetchall()

    stats = {
        "total": len(rows),
        "no_text": 0,
        "skipped_classification": 0,
        "skipped_classifier": 0,
        "cached": 0,
        "extracted": 0,
        "errors": 0,
        "pending": 0,
        "count_flags": 0,
        "split": 0,
        "chunks": 0,
    }

    extracted_count = 0
    for row in rows:
        if limit is not None and extracted_count >= limit:
            break

        sha256, url = row["sha256"], row["url"]
        from toronto_bids.pdf_text import source_text
        try:
            text = source_text(conn, row, recover=corpus == 'bd_report')
            conn.commit()
        except ValueError as exc:
            stats['errors'] += 1
            log(f"  FAILED text preparation {url}: {exc}")
            continue

        skip = _skip_reason(text, url, labels, classifier)
        if skip is not None:
            stats[skip] += 1
            if skip == "skipped_classifier":
                log(f"  classifier: skipped {url} (non-procurement)")
            continue

        if is_extracted(conn, sha256, EXTRACTOR_VERSION):
            stats["cached"] += 1
            continue

        extracted_count += 1
        try:
            chunks = split_document(text, max_chars=max_chars)
            if len(chunks) > 1:
                stats["split"] += 1
                stats["chunks"] += len(chunks)
                log(f"  splitting {url} into {len(chunks)} chunks")
            if hasattr(client, 'extract_document'):
                result = client.extract_document(sha256, chunks)
            elif len(chunks) > 1:
                all_contracts = []
                for chunk in chunks:
                    chunk_result = client.extract(chunk)
                    all_contracts.extend(chunk_result.get("contracts", []))
                result = {"contracts": dedup_contracts(all_contracts)}
            else:
                result = client.extract(text)
            flags = check_declared_counts(result)
            if flags:
                result["_flags"] = flags
                stats["count_flags"] += len(flags)
                for f in flags:
                    log(
                        f"  FLAG {url}: {f['reference']} declared {f['declared']}, "
                        f"got {f['actual']}"
                    )
            mark_extracted(
                conn, sha256, EXTRACTOR_VERSION, result_json=json.dumps(result)
            )
            stats["extracted"] += 1
            log(f"  extracted {url}")
        except ExtractionPending as exc:
            stats["pending"] += 1
            log(f"  PENDING {url}: {exc}")
        except (ValueError, TypeError, httpx.HTTPError) as exc:
            stats["errors"] += 1
            log(f"  FAILED {url}: {exc}")

    return stats


def _as_count(value) -> int | None:
    """Coerce whole-number int or digit string to int, ignoring bools and non-integers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def check_declared_counts(extraction: dict) -> list[dict]:
    """Flag contracts whose extracted bid count is below declared_submissions (overshoots allowed)."""
    flags = []
    for contract in extraction.get("contracts", []):
        declared = _as_count(contract.get("declared_submissions"))
        if declared is None:
            continue
        actual = len(contract.get("bids", []))
        if actual < declared:
            flags.append(
                {
                    "reference": contract.get("reference", ""),
                    "declared": declared,
                    "actual": actual,
                    "delta": actual - declared,
                }
            )
    return flags


_INCUMBENT_QUERIES = {
    "trca": {
        "bids": (
            "SELECT bp.url, ab.bidder_name_raw FROM agency_bid ab "
            "JOIN background_pdf bp ON LOWER(bp.url) = LOWER(ab.report_url) "
            "WHERE ab.source='trca_board'"
        ),
        "awards": (
            "SELECT bp.url, aa.supplier_name_raw FROM agency_award aa "
            "JOIN background_pdf bp ON LOWER(bp.url) = LOWER(aa.report_url) "
            "WHERE aa.source='trca_board'"
        ),
    },
    "ep": {
        "bids": (
            "SELECT bp.url, ab.bidder_name_raw FROM agency_bid ab "
            "JOIN background_pdf bp ON LOWER(bp.url) = LOWER(ab.report_url) "
            "WHERE ab.source='ep_board'"
        ),
        "awards": (
            "SELECT bp.url, aa.supplier_name_raw FROM agency_award aa "
            "JOIN background_pdf bp ON LOWER(bp.url) = LOWER(aa.report_url) "
            "WHERE aa.source='ep_board'"
        ),
    },
    "zoo": {
        "bids": (
            "SELECT bp.url, ab.bidder_name_raw FROM agency_bid ab "
            "JOIN background_pdf bp ON LOWER(bp.url) = LOWER(ab.report_url) "
            "WHERE ab.source='zoo_board'"
        ),
        "awards": (
            "SELECT bp.url, aa.supplier_name_raw FROM agency_award aa "
            "JOIN background_pdf bp ON LOWER(bp.url) = LOWER(aa.report_url) "
            "WHERE aa.source='zoo_board'"
        ),
    },
    "award_summary": {
        "bids": (
            "SELECT bp.url, b.bidder_name_raw FROM bid b "
            "JOIN background_pdf bp ON bp.document_number = b.document_number "
            "WHERE b.source='award_summary' AND bp.kind='award_summary'"
        ),
    },
    "committee": {
        "bids": (
            "SELECT bp.url, b.bidder_name_raw FROM bid b "
            "JOIN background_pdf bp ON bp.document_number = b.document_number "
            "WHERE b.source='committee_award' AND bp.kind='committee_award'"
        ),
    },
}


def _incumbent_by_doc(conn, corpus):
    """Return {url: set(normalized_name)} for the incumbent parser's output."""
    queries = _INCUMBENT_QUERIES.get(corpus, {})
    by_url = {}
    for query in queries.values():
        for row in conn.execute(query):
            url = row[0]
            name = _normalize_name(row[1])
            if name:
                by_url.setdefault(url, set()).add(name)
    return by_url


def _llm_names_from_result(result):
    """Extract normalized supplier names from an LLM extraction result."""
    names = set()
    for contract in result.get("contracts", []):
        for bid in contract.get("bids", []):
            if bid.get("supplier_name"):
                names.add(_normalize_name(bid["supplier_name"]))
        for award in contract.get("awards", []):
            if award.get("supplier_name"):
                names.add(_normalize_name(award["supplier_name"]))
    return names


def corpus_validation_report(conn, corpus) -> dict:
    """Compare cached LLM extractions against incumbent parser output for a corpus."""
    sql_where = CORPORA.get(corpus)
    if sql_where is None:
        raise ValueError(f"Unknown corpus {corpus!r}")

    rows = conn.execute(
        f"SELECT bp.sha256, bp.url, ec.result_json "
        f"FROM background_pdf bp "
        f"JOIN extraction_cache ec ON ec.sha256 = bp.sha256 "
        f"WHERE {sql_where} AND bp.sha256 IS NOT NULL "
        f"AND ec.extractor_version = ?",
        (EXTRACTOR_VERSION,),
    ).fetchall()

    llm_bids = 0
    llm_awards = 0
    llm_contracts = 0
    count_flags = 0
    docs_with_content = 0
    llm_by_url = {}

    for row in rows:
        result = json.loads(row["result_json"])
        contracts = result.get("contracts", [])
        llm_contracts += len(contracts)
        db = sum(len(c.get("bids", [])) for c in contracts)
        da = sum(len(c.get("awards", [])) for c in contracts)
        llm_bids += db
        llm_awards += da
        if db > 0 or da > 0:
            docs_with_content += 1
        count_flags += len(result.get("_flags", []))
        llm_by_url[row["url"]] = _llm_names_from_result(result)

    incumbent = _incumbent_by_doc(conn, corpus)
    shared_urls = set(llm_by_url) & set(incumbent)

    tp = fn = fp = 0
    for url in shared_urls:
        llm_set = llm_by_url[url]
        inc_set = incumbent[url]
        tp += len(llm_set & inc_set)
        fn += len(inc_set - llm_set)
        fp += len(llm_set - inc_set)

    incumbent_total = sum(len(v) for v in incumbent.values())

    return {
        "corpus": corpus,
        "extracted_docs": len(rows),
        "docs_with_content": docs_with_content,
        "count_flags": count_flags,
        "llm": {
            "contracts": llm_contracts,
            "bids": llm_bids,
            "awards": llm_awards,
            "unique_names": sum(len(v) for v in llm_by_url.values()),
        },
        "incumbent": {
            "total_names": incumbent_total,
            "docs_joinable": len(incumbent),
        },
        "comparison": {
            "shared_docs": len(shared_urls),
            "tp": tp,
            "fn": fn,
            "fp": fp,
            "recall": tp / (tp + fn) if (tp + fn) else None,
            "precision": tp / (tp + fp) if (tp + fp) else None,
        },
    }


def _normalize_name(name: str | None) -> str:
    if not name:
        return ""
    return " ".join(name.lower().split())


_CORPUS_SOURCE = {
    "ttc": "ttc_board",
    "trca": "trca_board",
    "ep": "ep_board",
    "zoo": "zoo_board",
    "award_summary": "award_summary",
    "committee": "committee_award",
    "composite": "bid_committee_composite",
    "ba_report": "ba_report",
    "bd_report": "bd_report",
}

_CORPUS_BUYER_SLUG = {
    "ttc": "toronto-transit-commission",
    "trca": "trca",
    "ep": "exhibition-place",
    "zoo": "toronto-zoo",
}


# Minimum share of previously extracted corpus docs required at current EXTRACTOR_VERSION before rebuilding.
_MIN_SWAP_COVERAGE = 0.5

# Map prompt amount_basis to bid.hst_basis ('net_of_taxes' stays NULL because it includes non-rebated HST).
_HST_BASIS_FROM_AMOUNT_BASIS = {
    "including_HST": "including",
    "plus_HST": "excluding",
}


def _hst_basis(bid: dict) -> str | None:
    return _HST_BASIS_FROM_AMOUNT_BASIS.get(bid.get("amount_basis"))


def _contract_document_number(contract: dict, fallback: str | None) -> str | None:
    """Extract a 10-digit document number from contract.reference, rejecting Call Numbers, else fallback."""
    from toronto_bids.linking.call_number import normalize_call_number
    from toronto_bids.linking.document_number import normalize_document_number

    ref = contract.get("reference")
    if not ref or normalize_call_number(ref):
        return fallback
    return normalize_document_number(ref) or fallback


def backfill_from_extraction(conn, corpus, *, log=lambda _m: None) -> dict:
    """Rebuild derived store tables from cached extractions via mark-and-sweep (db.rebuild_rows).

    Derives all rows before modifying tables in one transaction; empty derived sets delete nothing.
    Raises RuntimeError if current EXTRACTOR_VERSION coverage is below _MIN_SWAP_COVERAGE.
    """
    from toronto_bids.models import (
        AgencyAward,
        AgencyBid,
        AgencySolicitation,
        Bid,
        CompositeAward,
    )
    from toronto_bids.store.db import rebuild_rows, upsert_row
    from toronto_bids.pdf_text import source_text, record_for

    sql_where = CORPORA.get(corpus)
    if sql_where is None:
        raise ValueError(f"Unknown corpus {corpus!r}")

    source = _CORPUS_SOURCE[corpus]

    rows = conn.execute(
        f"SELECT bp.sha256, bp.url, bp.document_number, bp.reference, ec.result_json "
        f"FROM background_pdf bp "
        f"JOIN extraction_cache ec ON ec.sha256 = bp.sha256 "
        f"WHERE {sql_where} AND bp.sha256 IS NOT NULL "
        f"AND ec.extractor_version = ?",
        (EXTRACTOR_VERSION,),
    ).fetchall()

    # Count corpus docs ever extracted under any extractor_version.
    ever_extracted = conn.execute(
        f"SELECT COUNT(*) FROM background_pdf bp "
        f"WHERE {sql_where} AND bp.sha256 IS NOT NULL "
        f"AND EXISTS (SELECT 1 FROM extraction_cache ec WHERE ec.sha256 = bp.sha256)"
    ).fetchone()[0]
    if ever_extracted and len(rows) < _MIN_SWAP_COVERAGE * ever_extracted:
        raise RuntimeError(
            f"backfill {corpus}: only {len(rows)} of {ever_extracted} extracted documents "
            f"are cached at {EXTRACTOR_VERSION}; refusing to rebuild {source} rows from a "
            f"partial re-extraction (existing rows kept)"
        )

    buyer_id = None
    if corpus in _CORPUS_BUYER_SLUG:
        buyer_row = conn.execute(
            "SELECT id FROM buyer WHERE slug = ?",
            (_CORPUS_BUYER_SLUG[corpus],),
        ).fetchone()
        if buyer_row:
            buyer_id = buyer_row["id"]

    city_reviews = {}
    scoped_reviews = {}
    if corpus in ('ba_report','bd_report'):
        from toronto_bids.linking.city_cached_bids import reviewed_reports
        try:
            city_reviews = reviewed_reports(conn,corpus=corpus)
            from toronto_bids.linking.city_scoped_bids import reviewed_scoped_reports
            scoped_reviews = reviewed_scoped_reports(conn,corpus=corpus)
        except ValueError as exc:
            raise RuntimeError(f'backfill {corpus}: {exc}; existing rows kept') from exc

    # Derive rows before modifying tables.
    solicitations, bids, awards = [], [], []

    contracts_refused = 0
    decisions = []
    coverage = None
    admission = []

    def use_city_review(row):
        scoped = scoped_reviews.get(row['url'])
        if scoped is not None:
            proof,derived = scoped
            bids.extend(derived)
            admission.append(dict(reference=row['reference'],accepted=True,call_number=None,reason='source-reviewed scoped responses',url=row['url'],sha256=row['sha256'],prices_withheld=0,statuses_withheld=0,scoped_responses=len(proof['responses'])))
            return True
        reviewed = city_reviews.get(row['url'])
        if reviewed is None:
            return False
        proof,derived = reviewed
        raw = json.loads(row['result_json'])['contracts'][proof['contract_index']]['bids']
        bids.extend(derived)
        admission.append(dict(reference=row['reference'],accepted=True,call_number=None,
            reason='source-reviewed cached bid projection',url=row['url'],sha256=row['sha256'],
            prices_withheld=sum(bool(b.get('amount_raw')) and d.bid_price is None for b,d in zip(raw,derived)),
            statuses_withheld=sum(b.get('status') not in (None,'not_stated') for b in raw)))
        return True

    if corpus in ("trca", "ep", "zoo", "ttc"):
        from toronto_bids.linking.agency_reference import normalize_agency_reference, report_reference
        from toronto_bids.agency_coverage import decision
        from toronto_bids.linking.agency_identity import reviewed_identity, reviewed_bids, reviewed_decision_date, validate_representatives, IdentityReviewRequired

        if rows and buyer_id is None:
            raise RuntimeError(f'backfill {corpus}: buyer is not seeded; existing rows kept')

        try:
            validate_representatives(corpus,rows)
        except IdentityReviewRequired as exc:
            raise RuntimeError(f'backfill {corpus}: {exc}; existing rows kept') from exc

        # Holds follow exact source bytes across duplicate indexed URLs.
        award_review_contexts = {r["sha256"]: r["award_review_context_json"]
            for r in conn.execute("SELECT sha256, award_review_context_json FROM background_pdf "
                "WHERE sha256 IS NOT NULL AND award_review_context_json IS NOT NULL")}
        refusal_details = []
        for row in rows:
            result = json.loads(row["result_json"])
            contracts = result.get("contracts", [])
            for index, contract in enumerate(contracts):
                if not (contract.get("bids") or contract.get("awards")):
                    continue
                try:
                    identity_evidence = reviewed_identity(corpus, row['sha256'], contract,
                        report_url=row['url'], contract_count=len(contracts), contract_index=index,
                        result_sha256=hashlib.sha256(row['result_json'].encode()).hexdigest())
                    derived_date = reviewed_decision_date(contract, identity_evidence)
                    derived_bids = reviewed_bids(contract, identity_evidence)
                    claim_admission = None
                    if identity_evidence and {'award_review', 'participation_review'}.intersection(identity_evidence):
                        from toronto_bids.linking.agency_claim_review import admit_claims
                        claim_admission = admit_claims(conn, contract, identity_evidence)
                        if claim_admission is not None:
                            identity_evidence = dict(identity_evidence, award_admission=claim_admission)
                    ref = (identity_evidence['native_ref'] if identity_evidence is not None else
                           normalize_agency_reference(_CORPUS_BUYER_SLUG[corpus], contract.get("reference")))
                    if not ref:
                        if len(contracts) != 1:
                            raise ValueError("ambiguous reference-less multi-contract report")
                        ref = report_reference(row["reference"], row["url"])
                except IdentityReviewRequired as exc:
                    raise RuntimeError(f'backfill {corpus}: {exc}; existing rows kept') from exc
                except ValueError as exc:
                    contracts_refused += 1
                    refusal_details.append(f"{row['url']}: {exc}")
                    decisions.append(decision(row, corpus, buyer_id, index, contract, None, str(exc), len(contracts)))
                    continue
                withheld_awards_reason = (identity_evidence.get('withheld_awards_reason')
                                          if identity_evidence is not None else None)
                if corpus == 'ttc' and contract.get('awards'):
                    # TTC reports are recommendations; withhold awards until minutes/decisions are verified.
                    withheld_awards_reason = 'TTC report award recommendation requires independent decision verification'
                    identity_evidence = dict(identity_evidence or {}, withheld_awards_reason=withheld_awards_reason)
                if corpus == 'trca' and contract.get('awards') and row['sha256'] in award_review_contexts and claim_admission is None:
                    withheld_awards_reason = 'TRCA agenda recommendation requires independent decision verification'
                    identity_evidence = dict(identity_evidence or {},
                        withheld_awards_reason=withheld_awards_reason,
                        award_review_context=json.loads(award_review_contexts[row['sha256']]))
                if claim_admission and claim_admission['withheld']:
                    withheld_awards_reason = '; '.join(dict.fromkeys(c['reason'] for c in claim_admission['withheld']))
                if withheld_awards_reason:
                    contracts_refused += 1
                    refusal_details.append(f"{row['url']}: {withheld_awards_reason}")
                decisions.append(decision(row, corpus, buyer_id, index, contract, ref, withheld_awards_reason,
                                          len(contracts), identity_evidence))
                if identity_evidence and identity_evidence.get('representative_source'):
                    continue  # ledger-only confirmation; primary document derives facts
                admitted_awards = (claim_admission['accepted'] if claim_admission is not None else
                    [] if withheld_awards_reason else [{'award': a, 'award_date': derived_date} for a in contract.get('awards', [])])
                has_awards = bool(admitted_awards)
                solicitations.append(
                    AgencySolicitation(
                        buyer_id=buyer_id,
                        native_ref=ref,
                        title=contract.get("title"),
                        status="awarded" if has_awards else None,
                        posted_date=contract.get("posted_date"),
                        closing_date=contract.get("closed_date"),
                        portal_url=None,
                        source=source,
                    )
                )
                for bid in derived_bids:
                    name = bid.get("supplier_name")
                    if not name:
                        continue
                    bids.append(
                        AgencyBid(
                            buyer_id=buyer_id,
                            native_ref=ref,
                            bidder_name_raw=name,
                            bid_price=bid.get("amount_raw"),
                            hst_basis=_hst_basis(bid),
                            price_header=bid.get("price_header"),
                            status=bid.get("status"),
                            result_stage=("Tender submissions" if identity_evidence and identity_evidence.get("participation_review")
                                          else "Board report" if corpus == "ttc" else None),
                            report_url=row["url"],
                            source=source,
                        )
                    )
                for admitted in admitted_awards:
                    award = admitted['award']
                    name = award.get("supplier_name")
                    if not name:
                        continue
                    awards.append(
                        AgencyAward(
                            buyer_id=buyer_id,
                            native_ref=ref,
                            supplier_name_raw=name,
                            award_amount=award.get("amount_raw"),
                            value_confidential=1
                            if award.get("value_confidential")
                            else 0,
                            award_date=admitted['award_date'],
                            report_url=row["url"],
                            source=source,
                        )
                    )
        if refusal_details:
            log(f"  backfill {corpus}: refused facts in {contracts_refused} URL/contract observation(s): "
                + "; ".join(refusal_details[:10])
                + (" ..." if len(refusal_details) > 10 else ""))
        rebuilt = [("agency_bid", bids), ("agency_award", awards)]

    elif corpus == "bd_report":
        from toronto_bids.bd_admission import admit_report

        for row in rows:
            if use_city_review(row):
                continue
            text_record=record_for(conn,row['sha256'])
            if text_record and text_record['method']=='tesseract':
                admission.append(dict(reference=row['reference'], accepted=False, call_number=None,
                    reason='OCR-derived facts require an original-image source review', url=row['url'],
                    sha256=row['sha256'], prices_withheld=0, statuses_withheld=0))
                contracts_refused += 1
                continue
            context = conn.execute(
                "SELECT bp.*, ci.title FROM background_pdf bp "
                "LEFT JOIN council_item ci ON ci.reference=bp.reference WHERE bp.url=?",
                (row['url'],),
            ).fetchone()
            derived, decision = admit_report(
                reference=row['reference'], title=context['title'], text=source_text(conn, context),
                result=json.loads(row['result_json']),
            )
            decision.update(url=row['url'], sha256=row['sha256'])
            admission.append(decision)
            bids.extend(derived)
            contracts_refused += int(not decision['accepted'])
            log(f"  bd_report {row['reference']}: {decision['reason']}")
        rebuilt = [("bid", bids)]

    elif corpus == "ba_report":
        for row in rows:
            if use_city_review(row):
                continue
            result = json.loads(row["result_json"])
            for contract in result.get("contracts", []):
                # Key on council reference; include document_number only if it parses as a real 10-digit ID.
                doc_num = _contract_document_number(contract, None)
                for bid in contract.get("bids", []):
                    name = bid.get("supplier_name")
                    if not name:
                        continue
                    bids.append(
                        Bid(
                            bidder_name_raw=name,
                            reference=row["reference"],
                            document_number=doc_num,
                            bid_price=bid.get("amount_raw"),
                            hst_basis=_hst_basis(bid),
                            price_header=bid.get("price_header"),
                            status=bid.get("status"),
                            source=source,
                        )
                    )
        # BA-era awards are already on the OData spine.
        rebuilt = [("bid", bids)]

    elif corpus in ("award_summary", "committee"):
        for row in rows:
            result = json.loads(row["result_json"])
            for contract in result.get("contracts", []):
                # Committee reports can cover multiple contracts; Award Summary Forms map 1:1 to row.document_number.
                doc_num = (
                    _contract_document_number(contract, row["document_number"])
                    if corpus == "committee"
                    else row["document_number"]
                )
                for bid in contract.get("bids", []):
                    name = bid.get("supplier_name")
                    if not name:
                        continue
                    bids.append(
                        Bid(
                            bidder_name_raw=name,
                            document_number=doc_num,
                            bid_price=bid.get("amount_raw"),
                            hst_basis=_hst_basis(bid),
                            price_header=bid.get("price_header"),
                            status=bid.get("status"),
                            source=source,
                        )
                    )
        rebuilt = [("bid", bids)]

    elif corpus == "composite":
        from toronto_bids.linking.call_number import normalize_call_number

        refused, refused_refs = 0, []
        for row in rows:
            result = json.loads(row["result_json"])
            for contract in result.get("contracts", []):
                raw_ref = contract.get("reference")
                named = [a for a in contract.get("awards", []) if a.get("supplier_name")]
                # Refuse awards whose reference does not parse as a Call Number.
                call_number = normalize_call_number(raw_ref)
                if not call_number:
                    refused += len(named)
                    if named:
                        refused_refs.append(f"{row['reference']}: {raw_ref!r}")
                    continue
                for award in named:
                    name = award["supplier_name"]
                    awards.append(
                        CompositeAward(
                            call_number=call_number,
                            call_number_raw=raw_ref,
                            title=contract.get("title") or None,
                            reference=row["reference"],
                            supplier_name_raw=name,
                            award_value=award.get("amount_raw"),
                            source=source,
                        )
                    )
        if refused:
            log(
                f"  backfill composite: refused {refused} award(s) whose reference carries "
                f"no Call Number ({len(refused_refs)} contract(s)): "
                + "; ".join(refused_refs[:10])
                + (" ..." if len(refused_refs) > 10 else "")
            )
        rebuilt = [("composite_award", awards)]

    # Swap in one transaction; empty derived sets delete nothing.
    conn.commit()  # isolate swap rollback from prior caller work
    aliases = {"normalized": 0, "merged": 0}
    try:
        if corpus in _CORPUS_BUYER_SLUG and buyer_id is not None:
            from toronto_bids.linking.agency_reference import normalize_stored_references
            from toronto_bids.linking.agency_identity import history_aliases
            aliases = normalize_stored_references(conn, _CORPUS_BUYER_SLUG[corpus], buyer_id, source,
                                                   proven_aliases=history_aliases(decisions))
        for table, derived in rebuilt:
            if not derived:
                log(f"  backfill {corpus}: nothing derived for {table}, existing rows kept")
                continue
            if corpus == 'bd_report':
                # Only sweep admitted references so refused reports preserve earlier supported facts.
                accepted = sorted({d['reference'] for d in admission if d['accepted']})
                refused = {d['reference'] for d in admission if not d['accepted']}
                if refused & set(accepted):
                    raise RuntimeError('bd_report: mixed accepted/refused reports for an item; existing rows kept')
                placeholders = ','.join('?' for _ in accepted)
                rebuild_rows(conn, derived, scope=f"source = ? AND reference IN ({placeholders})",
                             params=(source, *accepted))
            else:
                rebuild_rows(conn, derived, scope="source = ?", params=(source,))
        if scoped_reviews:
            from toronto_bids.linking.city_scoped_bids import persist_scoped_reviews
            persist_scoped_reviews(conn,scoped_reviews)
        if city_reviews:
            from toronto_bids.linking.city_cached_bids import persist_evidence
            persist_evidence(conn,city_reviews)
        if corpus == 'trca':
            # Withdraw specific prior award rows rejected by a source-bound claim review.
            accepted = {(a.buyer_id, a.native_ref, a.supplier_name_raw, a.award_amount) for a in awards}
            for d in decisions:
                proof = json.loads(d['identity_evidence_json'] or 'null') or {}
                review = proof.get('award_admission')
                if review is None:
                    continue
                raw = json.loads(d['contract_json'])['awards']
                indices = set(review['amounts_withheld']) | {a['award_index'] for a in review['withheld']}
                for index in indices:
                    a = raw[index]
                    key = (buyer_id, d['native_ref'], a.get('supplier_name'), a.get('amount_raw'))
                    if key not in accepted:
                        conn.execute("DELETE FROM agency_award WHERE buyer_id=? AND native_ref=? "
                            "AND supplier_name_raw IS ? AND award_amount IS ? AND source=? AND report_url=?",
                            (*key, source, d['report_url']))
        # agency_solicitation is backfill-only (fills NULLs, never swept).
        for r in solicitations:
            upsert_row(conn, r, overwrite=False)
        if corpus == 'trca':
            # Clear board-derived 'awarded' status for held claims when no accepted award remains.
            held_refs = {d['native_ref'] for d in decisions if d['native_ref'] and d['reason']
                and d['identity_evidence_json'] and any(k in json.loads(d['identity_evidence_json'])
                    for k in ('award_admission', 'withheld_awards_reason'))}
            for ref in held_refs:
                conn.execute("UPDATE agency_solicitation SET status=NULL WHERE buyer_id=? "
                    "AND native_ref=? AND source=? AND status='awarded' AND NOT EXISTS "
                    "(SELECT 1 FROM agency_award a WHERE a.buyer_id=agency_solicitation.buyer_id "
                    "AND a.native_ref=agency_solicitation.native_ref)", (buyer_id, ref, source))
        if corpus in _CORPUS_BUYER_SLUG:
            from toronto_bids.agency_coverage import persist
            coverage = persist(conn, corpus, decisions, aliases, len(rows))
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    if coverage is not None:
        log(f"  identity {corpus}: {coverage['contracts_accepted']} accepted, "
            f"{coverage['contracts_refused']} refused contracts; "
            f"{coverage['refused_bid_facts']} bid / {coverage['refused_award_facts']} award facts affected; "
            f"{aliases['normalized']} stored aliases normalized, {aliases['merged']} merged; "
            f"{coverage['source_observations']} URL/contract observations "
            "(contracts deduplicated by document hash and contract index; tb agency-coverage for evidence)")
    return {
        "corpus": corpus,
        "docs_processed": len(rows),
        "contracts_refused": coverage['contracts_refused'] if coverage is not None else contracts_refused,
        "contract_observations_refused": contracts_refused,
        "aliases_normalized": aliases["normalized"],
        "aliases_merged": aliases["merged"],
        "solicitations_written": len(solicitations),
        "bids_written": len(bids),
        "awards_written": len(awards),
        "coverage": coverage,
        "admission": admission,
        "prices_withheld": sum(d["prices_withheld"] for d in admission),
        "statuses_withheld": sum(d["statuses_withheld"] for d in admission),
    }


def extract_and_backfill(
    conn, corpus, *, log=lambda _m: None, failures=None
) -> dict:
    """Extract uncached documents via LLM and backfill derived store tables.

    Skips API calls if credentials are unset and all documents are cached. Appends summary
    entries to failures for extraction errors and agency/bd_report coverage refusals.
    """
    from toronto_bids.config import CLASSIFICATION_LABELS_PATH
    from toronto_bids.extract import ExtractionClient

    labels = {}
    if CLASSIFICATION_LABELS_PATH.exists():
        labels = load_classification_labels(CLASSIFICATION_LABELS_PATH)

    try:
        client = ExtractionClient()
        stats = extract_corpus(conn, corpus, client=client, labels=labels, log=log)
        log(
            f"  extraction: {stats['extracted']} new, "
            f"{stats['cached']} cached, {stats['pending']} pending, "
            f"{stats['skipped_classifier']} skipped by classifier, {stats['errors']} errors, "
            f"{stats['count_flags']} count flags"
        )
        if stats["errors"] and failures is not None:
            attempted = stats["extracted"] + stats["errors"]
            failures.append(
                (
                    f"extract:{corpus}",
                    f"{stats['errors']} of {attempted} documents failed extraction",
                )
            )
    except ValueError:
        uncached = _count_uncached(conn, corpus, labels)
        if uncached > 0:
            raise
        log("  extraction: all documents cached, skipping API call")

    result = backfill_from_extraction(conn, corpus, log=log)
    coverage = result.get('coverage')
    if corpus == 'bd_report' and failures is not None and (result['contracts_refused'] or result['prices_withheld'] or result['statuses_withheld']):
        failures.append(('coverage:bd_report',
                         f"{result['contracts_refused']} report(s) refused; "
                         f"{result['prices_withheld']} qualified price(s) and {result['statuses_withheld']} status claim(s) withheld; "
                         "raw evidence retained; inspect admission decisions"))
    if coverage and failures is not None:
        withheld = coverage.get('contracts_withheld', 0)
        identity_refusals = coverage['contracts_refused'] - withheld
        if identity_refusals:
            failures.append((f'identity:{corpus}',
                         f"incomplete coverage: {identity_refusals} contract(s) have refused identity; "
                         f"{coverage['refused_bid_facts']} bid fact(s), "
                         f"{coverage['refused_award_facts'] - coverage.get('withheld_award_facts', 0)} award fact(s); "
                         "evidence retained — inspect with tb agency-coverage"))
        if withheld:
            failures.append((f'coverage:{corpus}',
                             f"incomplete coverage: {coverage['withheld_award_facts']} individual award claim(s) "
                             "withheld after source review; supported bids retained; inspect tb agency-coverage"))
    return result


def _count_uncached(conn, corpus, labels, classifier=_DEFAULT_CLASSIFIER) -> int:
    """Count uncached eligible documents using the same classifier gate as extract_corpus."""
    classifier = _resolve_classifier(corpus, classifier)
    sql_where = CORPORA[corpus]
    rows = conn.execute(
        f"SELECT * FROM background_pdf "
        f"WHERE {sql_where} AND sha256 IS NOT NULL",
    ).fetchall()
    count = 0
    for row in rows:
        from toronto_bids.pdf_text import source_text
        text = source_text(conn, row, recover=corpus == 'bd_report')
        conn.commit()
        if _skip_reason(text, row["url"], labels, classifier) is not None:
            continue
        if not is_extracted(conn, row["sha256"], EXTRACTOR_VERSION):
            count += 1
    return count


def validate_against_ground_truth(conn, ground_truth_path) -> dict:
    """Compare cached extractions against ground truth and return recall/precision metrics."""
    gt = json.loads(Path(ground_truth_path).read_text())
    results = []

    for doc in gt["documents"]:
        url = doc["url"]
        row = conn.execute(
            "SELECT sha256 FROM background_pdf WHERE url=?", (url,)
        ).fetchone()
        if not row:
            results.append({"id": doc["id"], "url": url, "status": "not_in_db"})
            continue

        cached = get_extraction(conn, row["sha256"], EXTRACTOR_VERSION)
        if not cached:
            results.append({"id": doc["id"], "url": url, "status": "not_extracted"})
            continue

        extraction = json.loads(cached)
        gt_entries = doc.get("entries", [])
        if doc.get("none_present"):
            gt_entries = []

        extracted_bids = []
        for contract in extraction.get("contracts", []):
            for bid in contract.get("bids", []):
                extracted_bids.append(
                    {
                        "supplier": _normalize_name(bid["supplier_name"]),
                        "contract": contract.get("reference", ""),
                    }
                )
            for award in contract.get("awards", []):
                extracted_bids.append(
                    {
                        "supplier": _normalize_name(award["supplier_name"]),
                        "contract": contract.get("reference", ""),
                    }
                )

        gt_set = {
            (_normalize_name(e["company"]), e.get("contract", "")) for e in gt_entries
        }
        extracted_set = {(b["supplier"], b["contract"]) for b in extracted_bids}

        tp = len(gt_set & extracted_set)
        fn = len(gt_set - extracted_set)
        fp = len(extracted_set - gt_set)
        recall = tp / len(gt_set) if gt_set else 1.0
        precision = tp / (tp + fp) if (tp + fp) else 1.0

        doc_result = {
            "id": doc["id"],
            "url": url,
            "status": "compared",
            "gt_count": len(gt_set),
            "extracted_count": len(extracted_set),
            "tp": tp,
            "fn": fn,
            "fp": fp,
            "recall": recall,
            "precision": precision,
            "missed": [
                {"supplier": s, "contract": c} for s, c in (gt_set - extracted_set)
            ],
        }
        flags = extraction.get("_flags", [])
        if flags:
            doc_result["count_flags"] = flags
        results.append(doc_result)

    compared = [r for r in results if r["status"] == "compared"]
    total_tp = sum(r["tp"] for r in compared)
    total_fn = sum(r["fn"] for r in compared)
    total_fp = sum(r["fp"] for r in compared)
    total_gt = sum(r["gt_count"] for r in compared)

    return {
        "documents": results,
        "aggregate": {
            "compared": len(compared),
            "total_gt": total_gt,
            "tp": total_tp,
            "fn": total_fn,
            "fp": total_fp,
            "recall": total_tp / total_gt if total_gt else 1.0,
            "precision": total_tp / (total_tp + total_fp)
            if (total_tp + total_fp)
            else 1.0,
        },
    }
