from pathlib import Path

from toronto_bids.models import CouncilItem
from toronto_bids.sources.council import parse_agenda_item

FIXTURES = Path(__file__).parent / "fixtures"


def _html():
    return (FIXTURES / "agenda_item.html").read_text()


def test_parses_item_title_and_decision():
    item, pdfs = parse_agenda_item(_html(), "2025.GG26.3")
    assert isinstance(item, CouncilItem)
    assert item.reference == "2025.GG26.3"
    assert item.title == "Agenda Item History - 2025.GG26.3"
    assert "suspend Capital Sewer" in item.decision_text


def test_dedups_pdf_links_and_classifies_kind():
    _item, pdfs = parse_agenda_item(_html(), "2025.GG26.3")
    urls = {p["url"] for p in pdfs}
    # 3 distinct PDFs (the 4th link duplicates backgroundfile-260581)
    assert len(pdfs) == 3
    assert "https://www.toronto.ca/legdocs/mmis/2025/gg/bgrd/backgroundfile-260581.pdf" in urls
    kinds = {p["url"].rsplit("/", 2)[-2]: p["kind"] for p in pdfs}
    assert kinds["bgrd"] == "bgrd"
    assert kinds["comm"] == "comm"


def test_missing_decision_section_is_none_safe():
    item, pdfs = parse_agenda_item("<html><head><title>Agenda Item History - X</title></head><body></body></html>", "X")
    assert item.reference == "X"
    assert item.decision_text is None
    assert pdfs == []
