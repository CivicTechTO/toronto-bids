from typing import Iterable

from lxml import html

from toronto_bids import config
from toronto_bids.models import SuspendedFirm
from toronto_bids.sources.base import Row


def parse_suspended_table(html_str: str) -> list[dict]:
    """Parse the first <table> into a list of header->cell dicts (one per body row)."""
    root = html.fromstring(html_str)
    tables = root.xpath("//table")
    if not tables:
        return []
    table = tables[0]
    headers = [th.text_content().strip() for th in table.xpath(".//thead//th")]
    rows = []
    for tr in table.xpath(".//tbody//tr"):
        cells = [td.text_content().strip() for td in tr.xpath("./td")]
        if not cells:
            continue
        if len(cells) != len(headers):
            raise ValueError(
                f"suspended-firms table row has {len(cells)} cells but {len(headers)} headers "
                f"(page structure changed): {cells}"
            )
        rows.append(dict(zip(headers, cells)))
    return rows


class SuspendedFirmsSource:
    name = "suspended_firms"
    overwrite = True

    def fetch(self, http) -> Iterable[dict]:
        page = http.get_text(config.SUSPENDED_FIRMS_URL)
        yield from parse_suspended_table(page)

    def normalize(self, raw: dict) -> Iterable[Row]:
        name = (raw.get("Supplier Name") or "").strip()
        if not name:
            return
        yield SuspendedFirm(
            supplier_name_raw=name,
            status=(raw.get("Status") or "").strip() or None,
            start_date=(raw.get("Start Date of Suspension") or "").strip() or None,
            end_date=(raw.get("End Date of Suspension") or "").strip() or None,
            suspension_type=(raw.get("Type of Suspension") or "").strip() or None,
            council_authority=(raw.get("Authority") or "").strip() or None,
            source="suspended_firms",
        )
