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


def extract_corpus(
    conn, corpus, *, client, labels, where=None, limit=None, log=lambda _m: None
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
    }

    extracted_count = 0
    for row in rows:
        if limit is not None and extracted_count >= limit:
            break

        sha256, text, url = row["sha256"], row["text"], row["url"]

        if not text:
            stats["no_text"] += 1
            continue

        if url in labels and not labels[url]:
            stats["skipped_classification"] += 1
            continue

        if is_extracted(conn, sha256, EXTRACTOR_VERSION):
            stats["cached"] += 1
            continue

        try:
            result = client.extract(text)
            mark_extracted(
                conn, sha256, EXTRACTOR_VERSION, result_json=json.dumps(result)
            )
            stats["extracted"] += 1
            extracted_count += 1
            log(f"  extracted {url}")
        except (ValueError, httpx.HTTPError) as exc:
            stats["errors"] += 1
            log(f"  FAILED {url}: {exc}")

    return stats


def _normalize_name(name: str) -> str:
    return " ".join(name.lower().split())


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

        results.append(
            {
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
        )

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
