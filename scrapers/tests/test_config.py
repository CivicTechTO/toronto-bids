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
    monkeypatch.setenv("ARIBA_PASSWORD", "your-ariba-supplier-username")
    assert _real_env("ARIBA_PASSWORD") == "your-ariba-supplier-username"
