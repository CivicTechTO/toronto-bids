"""`tb export`'s success line (#143).

`counts['solicitation']` is the CITY SPINE alone. On a DB that has agency data (agency
enrichment has run) but no City-spine `sync` data, the export correctly writes a full `buyers`
section, but the success line read `Exported 0 solicitations to <path>` — indistinguishable from
an export that genuinely did nothing. Realistically the deployed nightly always has City spine
data, so this only bites an agency-only / test run.
"""
from toronto_bids import cli
from toronto_bids.buyers import seed_buyers


def _run_export(monkeypatch, conn, tmp_path, capsys):
    from toronto_bids import config
    monkeypatch.setattr(cli, "_open_db", lambda: conn)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert cli.main(["export"]) == 0
    return capsys.readouterr().out


def test_an_agency_only_db_mentions_the_buyers_it_actually_exported(conn, monkeypatch, tmp_path, capsys):
    seed_buyers(conn)              # agency enrichment has run; tb sync never has
    out = _run_export(monkeypatch, conn, tmp_path, capsys)
    assert "0 solicitations + 3 agency buyer(s)" in out


def test_a_plain_city_spine_export_is_unchanged(conn, monkeypatch, tmp_path, capsys):
    """The overwhelming majority of exports: no agency enrichment has ever run, so `buyer` is
    empty and the line must read exactly as it always has -- no '+0 agency buyer(s)' clutter."""
    out = _run_export(monkeypatch, conn, tmp_path, capsys)
    assert "Exported 0 solicitations to" in out
    assert "agency buyer" not in out
