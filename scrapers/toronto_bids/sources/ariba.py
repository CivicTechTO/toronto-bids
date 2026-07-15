from typing import Iterable

from toronto_bids import config
from toronto_bids.sources.base import Row


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

    def normalize(self, raw: dict) -> Iterable[Row]:  # implemented in Task 4
        raise NotImplementedError
