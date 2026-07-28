"""`_select_all_attachments` — waiting for the select-all cascade to land (#174).

Doc5713434353 (Overlea Blvd Bridge RFT, 51 attachments) failed capture on eight consecutive
nights with "Could not select the attachments (header checkbox did not register)". The header
checkbox registered fine every time. The cascade that ticks the other 50 rows is an AJAX call
whose duration scales with the item count — **measured live at ~10.3s** — and the function
slept a flat 3000ms before counting, so it always read 1 and called it a failure.

Worse, the retry loop then clicked a *second* strategy, toggling the header back off mid-cascade
and restarting it. Three strategies x 3s meant the event could never succeed. Smaller events
cascade inside 3s, which is why 49 of 53 captured fine and this one never did.

So the rule: **poll for the cascade, never sleep a guess** — and only fall through to the next
strategy when the click genuinely did not register at all.
"""
import pytest

from toronto_bids.sources import ariba_attachments


class FakePage:
    """Minimal Playwright-page stand-in: the cascade lands after `cascade_polls` waits."""

    def __init__(self, total=51, cascade_polls=20, click_registers=True, bbox=True):
        self.total = total
        self.cascade_polls = cascade_polls
        self.click_registers = click_registers
        self.bbox = bbox
        self.clicks = 0
        self._waits_since_click = None
        self.mouse = self._Mouse(self)

    class _Mouse:
        def __init__(self, page):
            self.page = page

        def click(self, x, y):
            self.page._register_click()

    def _register_click(self):
        self.clicks += 1
        # A second click mid-cascade toggles the header back off and restarts — the real bug.
        self._waits_since_click = 0 if self.click_registers else None

    def locator(self, _selector):
        page = self

        class _Loc:
            first = None

            def bounding_box(self):
                return {"x": 10, "y": 20, "width": 24, "height": 24} if page.bbox else None

            def click(self, timeout=None):
                page._register_click()

        loc = _Loc()
        loc.first = loc
        return loc

    def wait_for_timeout(self, _ms):
        if self._waits_since_click is not None:
            self._waits_since_click += 1

    def evaluate(self, _js):
        """Checked count: 0 before any click, 1 while cascading, `total` once it lands."""
        if self._waits_since_click is None:
            return 0
        return self.total if self._waits_since_click >= self.cascade_polls else 1


def test_a_slow_cascade_is_waited_out_not_declared_a_failure():
    """The #174 regression: ~10s cascade must not be read as a dead checkbox."""
    page = FakePage(total=51, cascade_polls=20)

    ariba_attachments._select_all_attachments(page)

    assert page.evaluate("") == 51


def test_it_does_not_re_click_while_the_cascade_is_running():
    """Re-clicking mid-cascade toggles the header off — that is what made this unrecoverable."""
    page = FakePage(total=51, cascade_polls=20)

    ariba_attachments._select_all_attachments(page)

    assert page.clicks == 1, f"clicked {page.clicks}x; a second click restarts the cascade"


def test_a_fast_cascade_still_works():
    """Small events (the 49 that always captured) must be unaffected."""
    page = FakePage(total=6, cascade_polls=1)

    ariba_attachments._select_all_attachments(page)

    assert page.evaluate("") == 6
    assert page.clicks == 1


def test_a_click_that_never_registers_still_raises():
    """A genuinely dead checkbox must still fail loudly rather than hang or pass."""
    page = FakePage(click_registers=False)

    with pytest.raises(RuntimeError, match="did not select"):
        ariba_attachments._select_all_attachments(page, timeout_ms=2000)


def test_it_falls_through_to_the_next_strategy_when_there_is_no_bounding_box():
    """The mouse strategies need a bbox; without one it must try the others, not crash."""
    page = FakePage(total=8, cascade_polls=1, bbox=False)

    ariba_attachments._select_all_attachments(page, timeout_ms=5000)

    assert page.evaluate("") == 8      # reached via the label-click strategy


class TestParseTotalMb:
    """`parse_total_mb` — the 500 MB ceiling guard is only as good as this read (#174).

    Doc5713434353's picker showed `Total Size (MB): 792.41` while the guard read None, so the
    ceiling check was skipped and capture_event clicked a disabled Download button for 30s. The
    cause was invisible in a terminal: the label carries NON-BREAKING spaces and the value is
    separated by tabs. These strings are copied verbatim from the live page.
    """

    def test_the_real_page_text_that_defeated_the_old_pattern(self):
        # verbatim from the live picker, \xa0 and tabs included
        text = "Total\xa0Size\xa0(MB):\t\t792.41\nMax\xa0Size\xa0(MB):\t\t88.7\n"
        assert ariba_attachments.parse_total_mb(text) == 792.41

    def test_a_plain_space_layout_still_parses(self):
        assert ariba_attachments.parse_total_mb("Total Size (MB): 161.76") == 161.76

    def test_thousands_separators(self):
        assert ariba_attachments.parse_total_mb("Total\xa0Size\xa0(MB):\t1,234.5") == 1234.5

    def test_an_integer_total(self):
        assert ariba_attachments.parse_total_mb("Total Size (MB):\t\t0") == 0.0

    def test_absent_or_empty_reads_as_unknown_not_zero(self):
        assert ariba_attachments.parse_total_mb("Selected Items: 0") is None
        assert ariba_attachments.parse_total_mb("") is None
        assert ariba_attachments.parse_total_mb(None) is None

    def test_a_total_over_the_ceiling_is_comparable(self):
        """The whole point: 792.41 must exceed MAX_BUNDLE_MB so the event is skipped cleanly."""
        total = ariba_attachments.parse_total_mb("Total\xa0Size\xa0(MB):\t\t792.41")
        assert total > ariba_attachments.MAX_BUNDLE_MB
