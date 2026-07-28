"""Opening an attachment's menu, and revealing a hidden one (#183).

Two documents were being omitted from an archive that cannot be re-fetched, on every run:

    Doc5713434353   Part 1 ok   Part 2 FAILED   Part 3 ok    + schedule-b2.pdf FAILED
    Doc5540340341   Part 1 ok   Part 2 FAILED   Part 3 ok    + (reproduced live)

Works, fails, works — parity, not a bad row. A live probe confirmed both halves: the PML
triggers own mutually exclusive popup containers (opening one closes another), and a CLEAN
click on the very `Part 2` anchor that fails in a real run opens its menu in ~3s. So the row is
fine and the widget's state is not: `_dismiss_menu`'s Escape hides the previous menu while
AribaWeb still believes it is open, so the next trigger click is consumed as a close.

These tests pin the two behaviours that follow, with fakes rather than a browser:

  * a swallowed trigger click is retried, and the retry is refused when a menu is already
    visible (which would CLOSE it — the #174 trap, one widget over)
  * `_ensure_clickable` runs more than one re-expansion pass before giving up

The second one's MECHANISM is deliberately not asserted anywhere here — see
`_ensure_clickable`'s docstring. What is pinned is the observable: one pass was not enough.
"""
import pytest

from toronto_bids.sources import ariba_attachments as aa


class _FakeLocator:
    def __init__(self, page):
        self._page = page

    def click(self, timeout=None):
        self._page.clicks += 1
        self._page.on_click()

    def is_visible(self):
        return self._page.link_visible

    def scroll_into_view_if_needed(self, timeout=None):
        return None


class _FakePage:
    """Stands in for the bits `_open_attachment_menu` touches.

    `visible_after` is the click number at which the menu finally opens — 1 models a healthy
    trigger, 2 the swallowed-first-click that #183 measured, and None one that never opens.
    """

    def __init__(self, visible_after=1, preopen=0):
        self.clicks = 0
        self.visible_after = visible_after
        self._visible = preopen
        self.link_visible = True

    def on_click(self):
        if self.visible_after is not None and self.clicks >= self.visible_after:
            self._visible = 1

    def wait_for_timeout(self, _ms):
        return None

    # what `_visible_menu_items` drives
    def get_by_text(self, _text, exact=False):
        page = self

        class _Items:
            def count(self):
                return 3                       # three PMLs, three menus — as measured live

            def nth(self, i):
                class _N:
                    def is_visible(_self):
                        return i == 0 and page._visible > 0
                return _N()
        return _Items()


def _source(page):
    src = aa.AribaFileSource.__new__(aa.AribaFileSource)
    src.page = page
    src.log = lambda _m: None
    src._toggled = set()
    return src


FILE = {"name": "Part 2 - Construction Agreement_A1.pdf"}


# --- the swallowed trigger click ----------------------------------------------------------

def test_a_healthy_trigger_opens_on_the_first_click():
    """The retry must cost nothing on every document that already worked."""
    page = _FakePage(visible_after=1)
    src = _source(page)

    src._open_attachment_menu(FILE, _FakeLocator(page))

    assert page.clicks == 1


def test_a_swallowed_first_click_is_retried_and_succeeds():
    """The #183 measurement: click 1 opens nothing, click 2 opens the menu."""
    page = _FakePage(visible_after=2)
    src = _source(page)

    item = src._open_attachment_menu(FILE, _FakeLocator(page))

    assert page.clicks == 2
    assert item is not None


def test_a_trigger_that_never_opens_still_raises_naming_the_document():
    """A genuinely dead trigger must fail loudly, not hang or pass silently — the document is
    then recorded in the durable gap record rather than vanishing."""
    page = _FakePage(visible_after=None)
    src = _source(page)

    with pytest.raises(RuntimeError, match="the menu did not open"):
        src._open_attachment_menu(FILE, _FakeLocator(page))
    assert page.clicks == 2                     # bounded — not an unbounded click loop


def test_the_retry_is_refused_when_a_menu_is_already_visible():
    """The #174 trap, one widget over: a blind second click CLOSES what the first opened.

    Here the menu lands in the gap between `_await_menu_item` giving up and the retry firing.
    Clicking again would close it, so the retry must stand down and take what is there.
    """
    page = _FakePage(visible_after=None)
    src = _source(page)
    link = _FakeLocator(page)

    calls = []

    def _await(file, timeout_ms=15000):
        calls.append(1)
        if len(calls) == 1:
            # As live: gives up with nothing visible. The menu then lands on its own, in the
            # gap before the retry fires.
            page._visible = 1
            raise RuntimeError("the menu did not open within 15s (no VISIBLE x among 3)")
        return "the-late-item"

    src._await_menu_item = _await
    item = src._open_attachment_menu(FILE, link)

    assert page.clicks == 1, "clicked again while a menu was visible — that would close it"
    assert item == "the-late-item"              # took what was there rather than re-clicking


def test_visible_menu_items_reports_minus_one_when_the_dom_is_unreadable():
    """Callers use ZERO as permission to click. An unreadable DOM is an unanswered question,
    never permission."""
    class _Broken:
        def get_by_text(self, *_a, **_k):
            raise RuntimeError("Execution context was destroyed")

    src = _source(_Broken())
    assert src._visible_menu_items() == -1


# --- revealing a hidden control ------------------------------------------------------------

class _ExpandPage:
    """`link.is_visible()` flips to True only once `reveal_after` expansion passes have run."""

    def __init__(self, reveal_after):
        self.passes = 0
        self.reveal_after = reveal_after

    def visible(self):
        return self.reveal_after is not None and self.passes >= self.reveal_after


def _expanding_source(state):
    src = aa.AribaFileSource.__new__(aa.AribaFileSource)
    src.page = None
    src.log = lambda _m: None
    src._toggled = {"stale"}
    src._expand_references = lambda: state.__setattr__("passes", state.passes + 1) or 0
    return src


class _RevealLocator:
    def __init__(self, state):
        self._state = state

    def is_visible(self):
        return self._state.visible()

    def scroll_into_view_if_needed(self, timeout=None):
        return None


def test_a_control_already_visible_needs_no_expansion_pass():
    state = _ExpandPage(reveal_after=0)
    src = _expanding_source(state)

    src._ensure_clickable({"name": "a.pdf"}, _RevealLocator(state))

    assert state.passes == 0


def test_a_second_expansion_pass_runs_when_one_was_not_enough():
    """The #183 measurement: pass 1 left `Appendices ....zip` hidden and it was omitted; pass 2
    revealed it and the event went 3/4 -> 4/4. WHY is not asserted — see the docstring."""
    state = _ExpandPage(reveal_after=2)
    src = _expanding_source(state)

    src._ensure_clickable({"name": "Appendices.zip"}, _RevealLocator(state))

    assert state.passes == 2


def test_a_control_that_never_appears_raises_after_a_bounded_number_of_passes():
    state = _ExpandPage(reveal_after=None)
    src = _expanding_source(state)

    with pytest.raises(RuntimeError, match="not visible"):
        src._ensure_clickable({"name": "gone.pdf"}, _RevealLocator(state))
    assert state.passes == aa._EXPAND_ATTEMPTS      # bounded, not an unbounded sweep loop


def test_the_expansion_record_is_cleared_so_the_pass_can_act_at_all():
    """`_expand_references` skips any section already in `_toggled`, so a retry that did not
    clear it would re-run and do nothing at all."""
    state = _ExpandPage(reveal_after=1)
    src = _expanding_source(state)
    assert src._toggled                                  # starts non-empty

    src._ensure_clickable({"name": "a.pdf"}, _RevealLocator(state))

    assert not src._toggled
