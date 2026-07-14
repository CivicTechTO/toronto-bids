from typing import Iterable, Iterator

from toronto_bids import config
from toronto_bids.sources.base import Row


def resolve_resource_id(http, slug: str) -> str:
    """Resolve the datastore-active resource UUID for a CKAN dataset slug.

    Resource UUIDs rotate on refresh, so this must be called at runtime.
    """
    data = http.get_json(config.CKAN_BASE + "package_show", params={"id": slug})
    resources = data["result"]["resources"]
    for res in resources:
        if res.get("datastore_active"):
            return res["id"]
    raise LookupError(f"No datastore-active resource for CKAN dataset '{slug}'")


def fetch_datastore(http, resource_id: str, page_size: int = 10000) -> Iterator[dict]:
    """Yield every record from a CKAN datastore resource, paging by offset."""
    offset = 0
    while True:
        data = http.get_json(
            config.CKAN_BASE + "datastore_search",
            params={"resource_id": resource_id, "limit": page_size, "offset": offset},
        )
        records = data["result"]["records"]
        if not records:
            return
        yield from records
        offset += len(records)


class CkanSource:
    """A CKAN dataset adapter. `normalize` is implemented in Task 6."""

    overwrite = False  # CKAN backfills; OData is the spine.

    def __init__(self, name: str, slug: str):
        self.name = name
        self.slug = slug

    def fetch(self, http) -> Iterable[dict]:
        resource_id = resolve_resource_id(http, self.slug)
        yield from fetch_datastore(http, resource_id)

    def normalize(self, raw: dict) -> Iterable[Row]:  # completed in Task 6
        raise NotImplementedError
