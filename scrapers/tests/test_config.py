"""config.py's Ariba credential loading (#184).

`scrapers/.env.example` ships in git with placeholder values so the repo can stay public. On a
machine with no OTHER `.env` — a fresh checkout that copied the example but never filled it in —
`load_dotenv()` puts those placeholders into the real environment, and `os.environ.get(...)`
reads them back as non-empty strings. `capture_attachments`'s own "creds unset" guard checks
truthiness, which a placeholder satisfies, so it never fired: login() proceeded and died 30s
later inside Playwright with no hint why the fill failed. `_real_env` is the fix, tested in
isolation as a pure function — no need to touch the real environment or reload the module.
"""
from toronto_bids.config import _real_env


def test_the_placeholder_username_reads_as_unset(monkeypatch):
    monkeypatch.setenv("ARIBA_USERNAME", "your-ariba-supplier-username")
    assert _real_env("ARIBA_USERNAME") is None


def test_the_placeholder_password_reads_as_unset(monkeypatch):
    monkeypatch.setenv("ARIBA_PASSWORD", "your-ariba-supplier-password")
    assert _real_env("ARIBA_PASSWORD") is None


def test_a_real_credential_passes_through_unchanged(monkeypatch):
    monkeypatch.setenv("ARIBA_USERNAME", "actual.supplier@example.com")
    assert _real_env("ARIBA_USERNAME") == "actual.supplier@example.com"


def test_a_genuinely_unset_variable_stays_none(monkeypatch):
    monkeypatch.delenv("ARIBA_USERNAME", raising=False)
    assert _real_env("ARIBA_USERNAME") is None


def test_the_username_placeholder_does_not_blank_a_real_password(monkeypatch):
    """Each variable's placeholder is checked against ITS OWN known value — one placeholder
    string must never accidentally match the other variable's real credential."""
    monkeypatch.setenv("ARIBA_PASSWORD", "your-ariba-supplier-username")
    assert _real_env("ARIBA_PASSWORD") == "your-ariba-supplier-username"
