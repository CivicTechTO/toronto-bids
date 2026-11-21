from toronto_bids import cli
from toronto_bids.buyers import seed_buyers
from toronto_bids.models import AgencyAward, AgencySolicitation
from toronto_bids.store import db


_EP_GOT = {"solicitations": 107, "awards": 107, "bids": 115}
_EP_ROWS = (74, 88, 115)


def test_stored_line_reports_distinct_rows_not_the_upsert_count():
    line = cli._stored_line("ep", _EP_GOT, before=_EP_ROWS, after=_EP_ROWS)
    assert "74 solicitations (+0)" in line
    assert "88 awards (+0)" in line
    assert "115 bids (+0)" in line
    assert "107 solicitations" not in line
    assert "107 awards" not in line


def test_stored_line_still_surfaces_the_upsert_totals_but_labelled():
    line = cli._stored_line("ep", _EP_GOT, before=_EP_ROWS, after=_EP_ROWS)
    assert "[upserts 107/107/115]" in line


def test_stored_line_shows_growth_when_there_is_any():
    line = cli._stored_line("trca", {"solicitations": 108, "awards": 108, "bids": 118},
                            before=(70, 88, 115), after=(71, 89, 118))
    assert "71 solicitations (+1)" in line
    assert "89 awards (+1)" in line
    assert "118 bids (+3)" in line


def test_stored_line_delta_is_the_real_row_count_not_the_loop_count():
    line = cli._stored_line("ep", _EP_GOT, before=_EP_ROWS, after=_EP_ROWS)
    assert "awards (+0)" in line
    assert "+19" not in line


def test_stored_line_omits_bids_for_a_body_with_no_bid_table():
    line = cli._stored_line("zoo", {"solicitations": 5, "awards": 5},
                            before=(4, 4, 0), after=(4, 4, 0))
    assert "bids" not in line
    assert "[upserts 5/5]" in line


def test_source_row_counts_are_scoped_to_one_board(conn):
    ids = seed_buyers(conn)
    db.upsert_row(conn, AgencySolicitation(
        buyer_id=ids["trca"], native_ref="1", title=None, status=None, posted_date=None,
        closing_date=None, portal_url=None, source="trca_board"), overwrite=True)
    db.upsert_row(conn, AgencyAward(
        buyer_id=ids["trca"], native_ref="1", supplier_name_raw="X", award_amount=None,
        value_confidential=0, award_date=None, source="trca_board"), overwrite=True)
    db.upsert_row(conn, AgencyAward(
        buyer_id=ids["toronto-zoo"], native_ref="1", supplier_name_raw="Y", award_amount=None,
        value_confidential=0, award_date=None, source="zoo_board"), overwrite=True)
    assert cli._source_row_counts(conn, "trca_board") == (1, 1, 0)
    assert cli._source_row_counts(conn, "zoo_board") == (0, 1, 0)
    assert cli._source_row_counts(conn, "ep_board") == (0, 0, 0)


def test_source_row_counts_dedup_the_way_the_tables_do(conn):
    ids = seed_buyers(conn)
    for title in ("first report", "its amendment"):
        db.upsert_row(conn, AgencySolicitation(
            buyer_id=ids["trca"], native_ref="10039751", title=title, status=None,
            posted_date=None, closing_date=None, portal_url=None,
            source="trca_board"), overwrite=True)
    assert cli._source_row_counts(conn, "trca_board")[0] == 1


def _agencies_env(monkeypatch, conn, tmp_path):
    from toronto_bids import config
    monkeypatch.setattr(cli, "_open_db", lambda: conn)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cli, "_capture_agency_bodies", lambda *a, **k: [])
    from toronto_bids.linking import supplier
    monkeypatch.setattr(supplier, "build_supplier_dimension", lambda *a, **k: 0)


def test_only_ep_portal_completes_without_a_keyerror_or_a_portal_failure(
        conn, monkeypatch, tmp_path, capsys):
    _agencies_env(monkeypatch, conn, tmp_path)
    from toronto_bids.sources import bids_tenders
    called = []
    monkeypatch.setattr(bids_tenders, "run_portal_capture",
                        lambda *a, **k: called.append(k) or {})

    assert cli.main(["enrich-agencies", "--only", "ep", "--portal"]) == 0

    assert not called, "run_portal_capture must not run at all for a body with no portal"
    assert "no bids&tenders portal" in capsys.readouterr().out


def test_only_trca_portal_behaves_exactly_as_before(conn, monkeypatch, tmp_path):
    _agencies_env(monkeypatch, conn, tmp_path)
    from toronto_bids.sources import bids_tenders
    seen = {}
    def _fake(*_a, only=None, **_k):
        seen["only"] = only
        return {}
    monkeypatch.setattr(bids_tenders, "run_portal_capture", _fake)

    assert cli.main(["enrich-agencies", "--only", "trca", "--portal"]) == 0

    assert seen["only"] == {"trca"}


def test_only_zoo_portal_maps_to_the_toronto_zoo_slug(conn, monkeypatch, tmp_path):
    _agencies_env(monkeypatch, conn, tmp_path)
    from toronto_bids.sources import bids_tenders
    seen = {}
    def _fake(*_a, only=None, **_k):
        seen["only"] = only
        return {}
    monkeypatch.setattr(bids_tenders, "run_portal_capture", _fake)

    assert cli.main(["enrich-agencies", "--only", "zoo", "--portal"]) == 0

    assert seen["only"] == {"toronto-zoo"}


def test_no_only_portal_still_runs_every_enabled_body(conn, monkeypatch, tmp_path):
    _agencies_env(monkeypatch, conn, tmp_path)
    from toronto_bids.sources import bids_tenders
    seen = {}
    def _fake(*_a, only=None, **_k):
        seen["only"] = only
        return {}
    monkeypatch.setattr(bids_tenders, "run_portal_capture", _fake)

    assert cli.main(["enrich-agencies", "--portal"]) == 0

    assert seen["only"] is None


def test_capture_agency_bodies_isolates_a_failing_body(conn, monkeypatch):
    seed_buyers(conn)
    import toronto_bids.sources.trca_board as trca
    import toronto_bids.sources.zoo_board as zoo
    import toronto_bids.sources.ep_board as ep

    monkeypatch.setattr(trca, "store_trca_reports", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(zoo, "cached_zb_agendas", lambda *a, **k: {})
    monkeypatch.setattr(zoo, "store_zoo_reports", lambda *a, **k: {"solicitations": 0, "awards": 0})
    monkeypatch.setattr(ep, "cached_ep_agendas", lambda *a, **k: {})
    monkeypatch.setattr(ep, "store_ep_reports", lambda *a, **k: {"solicitations": 0, "awards": 0, "bids": 0})

    failures = cli._capture_agency_bodies(
        conn, bodies=["trca", "zoo", "ep"],
        fetch=False, scrape=False, virtual_display=False, out=lambda _m: None)

    assert [name for name, _ in failures] == ["trca"]
