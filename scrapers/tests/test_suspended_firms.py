from pathlib import Path

import httpx
import pytest

from toronto_bids.http import HttpClient
from toronto_bids.models import SuspendedFirm
from toronto_bids.sources.suspended_firms import SuspendedFirmsSource, parse_suspended_table

FIXTURES = Path(__file__).parent / "fixtures"


def _html():
    return (FIXTURES / "suspended_firms.html").read_text()


def test_parse_returns_one_dict_per_row_keyed_by_header():
    rows = parse_suspended_table(_html())
    assert len(rows) == 3
    assert rows[1] == {
        "Supplier Name": "Duron Ontario Ltd.",
        "Status": "Suspended",
        "Start Date of Suspension": "March 27, 2025",
        "End Date of Suspension": "March 27, 2030",
        "Type of Suspension": "Supplier Code of Conduct",
        "Authority": "2025.GG19.17",
    }


def test_parse_handles_na_and_multiday_dates_verbatim():
    rows = parse_suspended_table(_html())
    assert rows[2]["Status"] == "Permanent Suspension"
    assert rows[2]["Start Date of Suspension"] == "November 27, 28, and 29, 2012"
    assert rows[2]["End Date of Suspension"] == "N/A"
    assert rows[2]["Authority"] == "GM18.4"


def test_normalize_maps_row_dict_to_suspended_firm():
    rows = parse_suspended_table(_html())
    firm = list(SuspendedFirmsSource().normalize(rows[1]))[0]
    assert isinstance(firm, SuspendedFirm)
    assert firm.supplier_name_raw == "Duron Ontario Ltd."
    assert firm.status == "Suspended"
    assert firm.start_date == "March 27, 2025"
    assert firm.end_date == "March 27, 2030"
    assert firm.suspension_type == "Supplier Code of Conduct"
    assert firm.council_authority == "2025.GG19.17"
    assert firm.source == "suspended_firms"


def test_fetch_gets_page_and_yields_row_dicts():
    def handler(request):
        return httpx.Response(200, text=_html())
    http = HttpClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0.0)
    rows = list(SuspendedFirmsSource().fetch(http))
    assert len(rows) == 3
    assert rows[0]["Supplier Name"].startswith("Capital Sewers")


def test_source_attributes():
    src = SuspendedFirmsSource()
    assert src.name == "suspended_firms"
    assert src.overwrite is True


def test_parse_raises_on_cell_header_count_mismatch():
    # A row with fewer <td>s than <thead> columns must surface (not silently truncate).
    bad = """<html><body><table>
      <thead><tr><th>Supplier Name</th><th>Status</th><th>Authority</th></tr></thead>
      <tbody><tr><td>Acme</td><td>Suspended</td></tr></tbody>
    </table></body></html>"""
    with pytest.raises(ValueError):
        parse_suspended_table(bad)
