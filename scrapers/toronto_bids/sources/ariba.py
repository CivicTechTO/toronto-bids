import json
from typing import Iterable

from toronto_bids import config
from toronto_bids.linking.document_number import bridge_document_number
from toronto_bids.models import AribaPosting
from toronto_bids.sources.base import Row


def _clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _categories(search: dict, detail: dict | None) -> str | None:
    if detail and detail.get("categories"):
        names = [c.get("categoryName") for c in detail["categories"] if c.get("categoryName")]
        return json.dumps(names) if names else None
    cats = search.get("productsAndServicesCategories")
    return json.dumps(cats) if cats else None


def normalize_posting(raw: dict):
    search = raw["search"]
    detail = raw.get("detail")
    rfx_id = str(search["rfxID"])
    title = _clean(search.get("title")) or (_clean(detail.get("title")) if detail else None)
    external = _clean(detail.get("externalRfxId")) if detail else None

    if detail and detail.get("opportunityAmount"):
        amt = detail["opportunityAmount"]
        amount_min, amount_max = _clean(amt.get("minAmount")), _clean(amt.get("maxAmount"))
        currency = _clean(amt.get("currency"))
    else:
        amount_min, amount_max = _clean(search.get("minAmount")), _clean(search.get("maxAmount"))
        currency = None

    yield AribaPosting(
        rfx_id=rfx_id,
        document_number=bridge_document_number(external, title),
        title=title,
        posting_type=_clean(detail.get("type")) if detail else _clean(search.get("rfxType")),
        status=_clean(detail.get("status")) if detail else None,
        customer_name=_clean(search.get("customerName")),
        posted_date=_clean(search.get("datePosted")) or (_clean(detail.get("startDate")) if detail else None),
        close_date=_clean(search.get("endDate")) or (_clean(detail.get("endDate")) if detail else None),
        categories=_categories(search, detail),
        amount_min=amount_min,
        amount_max=amount_max,
        currency=currency,
        public_posting_url=_clean(detail.get("publicPostingUrl")) if detail else None,
        sourcing_url=_clean(detail.get("sourcingUrl")) if detail else None,
        external_rfx_id=external,
        raw_json=json.dumps(detail) if detail else None,
        source="ariba_discovery",
    )


class AribaDiscoverySource:
    """Archives open City-of-Toronto SAP Ariba Discovery postings via public JSON APIs."""

    name = "ariba_discovery"
    overwrite = True  # later successful detail fills NULLs; a later 500 never wipes a snapshot.

    def fetch(self, http) -> Iterable[dict]:
        data = http.post_json(
            config.ARIBA_SEARCH_URL,
            json=config.ARIBA_SEARCH_BODY,
            params=config.ARIBA_SEARCH_PARAMS,
        )
        for record in data.get("solarRecords", []):
            if record.get("customerName") != config.ARIBA_CUSTOMER_NAME:
                continue
            detail = None
            try:
                detail = http.get_json(
                    config.ARIBA_DETAIL_URL.format(rfx_id=record["rfxID"]),
                    headers={"Accept": "application/json"},
                )
            except Exception:
                # Per-posting isolation: ~40% of details 500. Archive the search
                # metadata anyway; a later run's detail call fills the gap.
                detail = None
            yield {"search": record, "detail": detail}

    def normalize(self, raw: dict) -> Iterable[Row]:
        yield from normalize_posting(raw)
