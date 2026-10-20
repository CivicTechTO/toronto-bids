import base64
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
    if detail and detail.get("rfxEventSupCommodity"):
        names = [c.get("commodityName") for c in detail["rfxEventSupCommodity"] if c.get("commodityName")]
        return json.dumps(names) if names else None
    cats = search.get("productsAndServicesCategories")
    return json.dumps(cats) if cats else None


def normalize_posting(raw: dict):
    search = raw["search"]
    detail = raw.get("detail")
    rfx_id = str(search["rfxID"])
    title = _clean(search.get("title")) or (_clean(detail.get("eventName")) if detail else None)
    external = _clean(detail.get("rfxIdExternal")) if detail else None

    if detail and detail.get("upperLimit_amount") is not None:
        amount_min = _clean(detail.get("lowerLimit_amount"))
        amount_max = _clean(detail.get("upperLimit_amount"))
        currency = _clean(detail.get("upperLimit_currency"))
    else:
        amount_min, amount_max = _clean(search.get("minAmount")), _clean(search.get("maxAmount"))
        # Search amounts are converted USD estimates; preserve the existing raw-only fallback.
        currency = None

    yield AribaPosting(
        rfx_id=rfx_id,
        document_number=bridge_document_number(external, title),
        title=title,
        posting_type=_clean(detail.get("rfxType")) if detail else _clean(search.get("adRfxType")),
        status=_clean(detail.get("stateName")) if detail else None,
        customer_name=(_clean(detail.get("orgUnitName")) if detail else None)
        or _clean(search.get("customerName")) or config.ARIBA_CUSTOMER_NAME,
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
        # Mirror the anonymous Explore Leads request, including its organization filter.
        # Migrated search records often omit customerName, so free-text is not a buyer filter.
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        page, cursor, seen = 1, [], set()
        while True:
            filters = {
                "page": page, "pageSize": config.ARIBA_SEARCH_PAGE_SIZE,
                "leadType": None, "publicSector": None,
                "minAmount": None, "maxAmount": None,
                "postedDate": None, "deadlineDate": None, "humanitarianAid": [],
                "freeText": "", "sortModel": "RELEVANCE", "searchType": "ALL_LEADS",
                "searchAfter": cursor, "commodities": [], "territories": [],
                "orgAnid": config.ARIBA_ORG_ANID,
            }
            encoded = base64.b64encode(json.dumps(filters, separators=(",", ":")).encode()).decode()
            data = http.get_json(config.ARIBA_SEARCH_URL, params={"filter": encoded}, headers=headers)
            if (not isinstance(data, dict) or not isinstance(data.get("records"), list)
                    or type(data.get("totalNumberOfRecords")) is not int
                    or data["totalNumberOfRecords"] < 0):
                raise ValueError("Ariba search response missing records or totalNumberOfRecords")
            records, total = data["records"], data["totalNumberOfRecords"]
            if not records and len(seen) < total:
                raise ValueError("Ariba search ended before all records were fetched")
            for record in records:
                rfx_id = str(record.get("rfxID", ""))
                if not rfx_id.isdigit() or rfx_id in seen:
                    raise ValueError("Ariba search returned a missing or repeated posting ID")
                seen.add(rfx_id)
                if record.get("customerName") not in (None, config.ARIBA_CUSTOMER_NAME):
                    continue
                detail = None
                try:
                    detail = http.get_json(
                        config.ARIBA_DETAIL_URL.format(rfx_id=rfx_id),
                        params={"$expand": "rfxEventSupCommodity,rfxEventTerritory,humanitarianAid"},
                        headers=headers,
                    )
                except Exception:
                    # Per-posting isolation: archive search metadata on a detail failure.
                    # NULL-preserving upserts retain any previously captured snapshot.
                    detail = None
                if detail is not None:
                    if not isinstance(detail, dict) or str(detail.get("ID")) != rfx_id:
                        raise ValueError("Ariba detail returned a missing or mismatched posting ID")
                    if detail.get("anid") != config.ARIBA_ORG_ANID:
                        raise ValueError("Ariba detail returned a different buyer identity")
                yield {"search": record, "detail": detail}
            if len(seen) >= total:
                return
            next_cursor = data.get("searchAfter")
            if not isinstance(next_cursor, list) or not next_cursor or next_cursor == cursor:
                raise ValueError("Ariba search missing an advancing pagination cursor")
            page, cursor = page + 1, next_cursor

    def normalize(self, raw: dict) -> Iterable[Row]:
        yield from normalize_posting(raw)
