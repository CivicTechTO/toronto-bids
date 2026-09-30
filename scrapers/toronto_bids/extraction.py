"""Extraction orchestrator — classification gate, cache, and corpus iteration.

Ties together the extraction client (#208), the extraction cache (#207), and the
machine-label classification gate to extract bids from all six corpora through
one prompt.
"""

import json
from pathlib import Path

import httpx

from toronto_bids.extract import EXTRACTOR_VERSION
from toronto_bids.store.db import get_extraction, is_extracted, mark_extracted

CORPORA = {
    "trca": "kind='agency_board' AND url LIKE '%escribemeetings%'",
    "ep": "kind='agency_board' AND url LIKE '%/ep/%'",
    "zoo": "kind='agency_board' AND url LIKE '%/zb/%'",
    "award_summary": "kind='award_summary'",
    "committee": "kind='committee_award'",
    "composite": "kind='bgrd'",
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


def _skip_reason(text, url, labels) -> str | None:
    """Why a held document is never sent for extraction, or None if it is eligible.

    Shared by `extract_corpus` and `_count_uncached` so the two cannot drift (#220):
    a document extraction never caches must never count as uncached.
    """
    if not text:
        return "no_text"
    if url in labels and not labels[url]:
        return "skipped_classification"
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
):
    """Extract bids from all qualifying documents in a corpus.

    Returns a stats dict with counts of what happened.
    """
    sql_where = where or CORPORA.get(corpus)
    if sql_where is None:
        raise ValueError(
            f"Unknown corpus {corpus!r}, expected one of: {', '.join(sorted(CORPORA))}"
        )
    query = (
        f"SELECT sha256, text, url FROM background_pdf "
        f"WHERE {sql_where} AND sha256 IS NOT NULL "
        f"ORDER BY url"
    )
    rows = conn.execute(query).fetchall()

    stats = {
        "total": len(rows),
        "no_text": 0,
        "skipped_classification": 0,
        "cached": 0,
        "extracted": 0,
        "errors": 0,
        "count_flags": 0,
        "split": 0,
        "chunks": 0,
    }

    extracted_count = 0
    for row in rows:
        if limit is not None and extracted_count >= limit:
            break

        sha256, text, url = row["sha256"], row["text"], row["url"]

        skip = _skip_reason(text, url, labels)
        if skip is not None:
            stats[skip] += 1
            continue

        if is_extracted(conn, sha256, EXTRACTOR_VERSION):
            stats["cached"] += 1
            continue

        try:
            chunks = split_document(text, max_chars=max_chars)
            if len(chunks) > 1:
                stats["split"] += 1
                stats["chunks"] += len(chunks)
                log(f"  splitting {url} into {len(chunks)} chunks")
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
            extracted_count += 1
            log(f"  extracted {url}")
        # TypeError too (#219): a response of an unexpected shape is one document's
        # refusal, never a crash that blocks every uncached document after it.
        except (ValueError, TypeError, httpx.HTTPError) as exc:
            stats["errors"] += 1
            log(f"  FAILED {url}: {exc}")

    return stats


def _as_count(value) -> int | None:
    """A declared count as an int, or None when the model gave nothing usable (#219).

    Models sometimes return `"8"` for `8`; that is coerced. Anything else that is not a
    whole number (`"eight"`, `8.5`, `true`, a list) is ignored rather than guessed at.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def check_declared_counts(extraction: dict) -> list[dict]:
    """Compare declared_submissions against actual bid count per contract.

    Returns a list of flags for contracts where the bid count falls SHORT
    of the declared count. Overshoots are kept — the declaration sometimes
    covers only compliant bids while the table lists everyone.
    """
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
    "trca": "trca_board",
    "ep": "ep_board",
    "zoo": "zoo_board",
    "award_summary": "award_summary",
    "committee": "committee_award",
    "composite": "bid_committee_composite",
}

_CORPUS_BUYER_SLUG = {
    "trca": "trca",
    "ep": "exhibition-place",
    "zoo": "toronto-zoo",
}


# A swap needs the current extractor version to cover at least this share of the documents
# that were ever extracted for the corpus. Below it, the cache is mid-migration (a prompt edit
# bumped EXTRACTOR_VERSION and re-extraction has not caught up — or failed), and swapping would
# replace the whole archive with whatever fraction was re-extracted. Deliberately low, like
# ariba_files' _MIN_CAPTURE_RATIO (#182), so a few permanently failing documents cannot freeze
# the tables; a near-total shortfall can only mean the re-extraction broke.
_MIN_SWAP_COVERAGE = 0.5


def backfill_from_extraction(conn, corpus, *, log=lambda _m: None) -> dict:
    """Populate store tables from cached LLM extractions.

    The tables are DERIVED from the cache, so each source's rows are rebuilt rather than
    diff-upserted — the sanctioned exception to "rows are never deleted" that
    `build_supplier_dimension` and `enrich-ariba-attachments --reindex` also take. This is a
    permanent contract, not a migration: every parser or prompt fix self-heals by re-deriving.
    **Derive first, delete only on success.** Every row is built before anything is deleted;
    a table whose derived set is empty is left untouched (an empty set deletes nothing — a
    machine holding no cached extractions must not erase the archive); and the delete +
    insert run in one transaction, rolled back on any error.

    Raises RuntimeError, writing nothing, when fewer than `_MIN_SWAP_COVERAGE` of the
    corpus's previously extracted documents are cached at the current EXTRACTOR_VERSION —
    the state right after a prompt edit, before re-extraction has caught up.
    """
    from toronto_bids.models import (
        AgencyAward,
        AgencyBid,
        AgencySolicitation,
        Bid,
        CompositeAward,
    )
    from toronto_bids.store.db import upsert_row

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

    # Same basis as `rows` (background_pdf rows, not distinct hashes), at ANY version: the
    # documents whose extractions fed the tables at some point.
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

    # ── derive: build every row before touching any table ──
    solicitations, bids, awards = [], [], []

    if corpus in ("trca", "ep", "zoo"):
        for row in rows:
            result = json.loads(row["result_json"])
            for contract in result.get("contracts", []):
                ref = contract.get("reference", "")
                if not ref:
                    continue
                has_awards = bool(contract.get("awards"))
                solicitations.append(
                    AgencySolicitation(
                        buyer_id=buyer_id,
                        native_ref=ref,
                        title=contract.get("title"),
                        status="awarded" if has_awards else None,
                        posted_date=None,
                        closing_date=None,
                        portal_url=None,
                        source=source,
                    )
                )
                for bid in contract.get("bids", []):
                    name = bid.get("supplier_name")
                    if not name:
                        continue
                    bids.append(
                        AgencyBid(
                            buyer_id=buyer_id,
                            native_ref=ref,
                            bidder_name_raw=name,
                            bid_price=bid.get("amount_raw"),
                            report_url=row["url"],
                            source=source,
                        )
                    )
                for award in contract.get("awards", []):
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
                            award_date=None,
                            report_url=row["url"],
                            source=source,
                        )
                    )
        rebuilt = [("agency_bid", bids), ("agency_award", awards)]

    elif corpus in ("award_summary", "committee"):
        for row in rows:
            doc_num = row["document_number"]
            result = json.loads(row["result_json"])
            for contract in result.get("contracts", []):
                for bid in contract.get("bids", []):
                    name = bid.get("supplier_name")
                    if not name:
                        continue
                    bids.append(
                        Bid(
                            bidder_name_raw=name,
                            document_number=doc_num,
                            bid_price=bid.get("amount_raw"),
                            source=source,
                        )
                    )
        rebuilt = [("bid", bids)]

    elif corpus == "composite":
        for row in rows:
            result = json.loads(row["result_json"])
            for contract in result.get("contracts", []):
                call_number = contract.get("reference", "")
                if not call_number:
                    continue
                for award in contract.get("awards", []):
                    name = award.get("supplier_name")
                    if not name:
                        continue
                    awards.append(
                        CompositeAward(
                            call_number=call_number,
                            reference=row["reference"],
                            supplier_name_raw=name,
                            award_value=award.get("amount_raw"),
                            source=source,
                        )
                    )
        rebuilt = [("composite_award", awards)]

    # ── swap: one transaction; an empty derived set deletes nothing ──
    conn.commit()  # so a rollback below undoes the swap alone, never a caller's work
    try:
        for table, derived in rebuilt:
            if not derived:
                log(f"  backfill {corpus}: nothing derived for {table}, existing rows kept")
                continue
            conn.execute(f"DELETE FROM {table} WHERE source = ?", (source,))
            for r in derived:
                upsert_row(conn, r, overwrite=True)
        # agency_solicitation is backfill-only (fills NULLs, never deleted), so no swap.
        for r in solicitations:
            upsert_row(conn, r, overwrite=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        "corpus": corpus,
        "docs_processed": len(rows),
        "solicitations_written": len(solicitations),
        "bids_written": len(bids),
        "awards_written": len(awards),
    }


def extract_and_backfill(conn, corpus, *, log=lambda _m: None) -> dict:
    """Extract any uncached documents via LLM, then backfill store tables.

    If OPENROUTER_API_KEY is unset and all documents are already cached,
    the extraction step is skipped and only backfill runs.
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
            f"{stats['cached']} cached, {stats['errors']} errors"
        )
    except ValueError:
        uncached = _count_uncached(conn, corpus, labels)
        if uncached > 0:
            raise
        log("  extraction: all documents cached, skipping API call")

    return backfill_from_extraction(conn, corpus, log=log)


def _count_uncached(conn, corpus, labels) -> int:
    """Count documents in a corpus that are not yet in the extraction cache."""
    sql_where = CORPORA[corpus]
    rows = conn.execute(
        f"SELECT sha256, text, url FROM background_pdf "
        f"WHERE {sql_where} AND sha256 IS NOT NULL",
    ).fetchall()
    count = 0
    for row in rows:
        if _skip_reason(row["text"], row["url"], labels) is not None:
            continue
        if not is_extracted(conn, row["sha256"], EXTRACTOR_VERSION):
            count += 1
    return count


def validate_against_ground_truth(conn, ground_truth_path) -> dict:
    """Compare cached extractions against human-labelled ground truth.

    Returns per-document and aggregate recall/precision.
    """
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
