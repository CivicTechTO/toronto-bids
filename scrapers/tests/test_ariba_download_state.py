"""A saved download can leave Ariba's request gate busy (#183)."""
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from toronto_bids.sources import ariba_files
from toronto_bids.sources import ariba_attachments as aa


class _Page:
    def __init__(self, stuck=False):
        self.busy = False
        self.stuck = stuck
        self.elapsed = 0
        self.opened = None
        self.download_name = None
        self.keyboard = SimpleNamespace(press=lambda key: None)

    def evaluate(self, script):
        assert "isRequestInProgress" in script
        return self.busy

    def wait_for_timeout(self, milliseconds):
        self.elapsed += milliseconds
        if not self.stuck and self.elapsed >= 1500:
            self.busy = False

    def expect_download(self, **kwargs):
        return _Download(self)


class _Download:
    def __init__(self, page):
        self.page = page
        self.value = self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if args[0] is None and self.page.download_name is None:
            raise TimeoutError('no download started')

    def save_as(self, dest):
        Path(dest).write_text(self.page.download_name)
        self.page.download_name = None
        self.page.busy = True
        self.page.elapsed = 0


def _source(page):
    source = aa.AribaFileSource.__new__(aa.AribaFileSource)
    source.page = page
    source._require_event_view = lambda action: None
    source._await_menu_clear = lambda file: None
    source._close_popups = lambda: None

    def resolve(file):
        def click(**kwargs):
            # Ariba suppresses the action while the preceding request is outstanding.
            if not page.busy:
                page.opened = file['name']
                if file.get('kind') == ariba_files.MENU_ITEM:
                    page.download_name = file['name']
        return SimpleNamespace(click=click)

    source._resolve_anchor = resolve
    source._ensure_clickable = lambda file, link: None

    def menu(file):
        if page.opened != file['name']:
            raise RuntimeError('no VISIBLE Download this attachment')
        return SimpleNamespace(click=lambda: setattr(page, 'download_name', file['name']))

    source._await_menu_item = menu
    return source


@pytest.mark.parametrize('kind', [ariba_files.TREE_LINK, ariba_files.MENU_ITEM])
def test_next_document_waits_for_ariba_after_previous_bytes_are_saved(tmp_path, kind):
    page = _Page()
    source = _source(page)
    source.download({'name': 'first.pdf', 'kind': ariba_files.TREE_LINK}, tmp_path / 'first')
    assert page.busy  # save_as completes before Ariba's own download callback
    source.download({'name': 'second.pdf', 'kind': kind}, tmp_path / 'second')
    assert (tmp_path / 'first').read_text() == 'first.pdf'
    assert (tmp_path / 'second').read_text() == 'second.pdf'


def test_stuck_request_refuses_to_click_the_next_document(tmp_path):
    page = _Page(stuck=True)
    page.busy = True
    with pytest.raises(RuntimeError, match='request.*still.*progress'):
        _source(page).download({'name': 'next.pdf'}, tmp_path / 'next')
    assert page.opened is None
    assert not (tmp_path / 'next').exists()


class _Menu:
    def __init__(self, page, name):
        self.page, self.name = page, name

    def get_attribute(self, name):
        assert name == 'id'
        return self.name

    def wait_for(self, state, **kwargs):
        assert (self.page.active == self.name) == (state == 'visible')


class _Collection:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    @property
    def first(self):
        return self.items[0]

    def all(self):
        return self.items

    def evaluate_all(self, script):
        assert 'id' in script
        return [item.name for item in self.items]

    def click(self, **kwargs):
        assert len(self.items) == 1
        self.items[0].click(**kwargs)

    def press(self, key, **kwargs):
        assert len(self.items) == 1
        self.items[0].press(key, **kwargs)


class _PopupPage:
    def __init__(self):
        self.active = 'second'
        self.clicked = []
        self.dismissals = 0
        self.owners = {'first': 1, 'second': 1}

    def locator(self, selector):
        if selector == '.awmenu:visible':
            return _Collection([_Menu(self, self.active)] if self.active else [])
        if selector.startswith('[id='):
            return _Menu(self, json.loads(selector[len('[id='):-1]))
        prefix = 'a[bh="PML"][_mid='
        assert selector.startswith(prefix)
        name = json.loads(selector[len(prefix):-1])

        def click(**kwargs):
            self.clicked.append(name)
            self.active = None if self.active == name else name

        def pointer_click(**kwargs):
            if self.active:
                raise RuntimeError('References popup intercepts pointer events')
            click(**kwargs)

        def press(key, **kwargs):
            raise RuntimeError('Enter may select a menu item instead of closing the popup')

        return _Collection([
            SimpleNamespace(click=pointer_click, press=press)] * self.owners[name])

    def evaluate(self, script):
        assert 'hideActiveMenu' in script
        self.active = None
        self.dismissals += 1


class _Reference:
    def __init__(self, page, menu):
        self.page, self.menu = page, menu

    def is_visible(self):
        return self.page.active == self.menu

    def scroll_into_view_if_needed(self, **kwargs):
        assert self.is_visible()

    def locator(self, selector):
        assert 'ancestor::' in selector and 'awmenu' in selector
        return _Collection([_Menu(self.page, self.menu)])

    def wait_for(self, state, **kwargs):
        assert state == 'visible' and self.is_visible()


def _popup_source(page):
    source = aa.AribaFileSource.__new__(aa.AribaFileSource)
    source.page = page
    source._toggled = set()
    # Re-expanding every References control leaves only the last menu visible.
    source._expand_references = lambda: setattr(page, 'active', 'second')
    return source


def test_hidden_reference_reopens_its_own_menu_instead_of_the_last_menu():
    page = _PopupPage()
    source = _popup_source(page)
    for menu in ['first', 'second', 'first']:
        source._ensure_clickable(
            {'name': menu + '.pdf', 'kind': ariba_files.MENU_ITEM}, _Reference(page, menu))
        assert page.active == menu
    assert page.clicked == ['first', 'second', 'first']


def test_open_reference_popup_is_closed_through_the_widget_without_selecting_an_item():
    page = _PopupPage()
    _popup_source(page)._close_popups()
    assert page.active is None
    assert page.clicked == []
    assert page.dismissals == 1


@pytest.mark.parametrize('owners', [0, 2])
def test_missing_or_ambiguous_menu_owner_refuses_to_guess(owners):
    page = _PopupPage()
    page.owners['first'] = owners
    with pytest.raises(RuntimeError, match='owner'):
        _popup_source(page)._ensure_clickable(
            {'name': 'first.pdf', 'kind': ariba_files.MENU_ITEM}, _Reference(page, 'first'))
    assert page.clicked == []
