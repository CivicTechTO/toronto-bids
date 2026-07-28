from toronto_bids import cli
from toronto_bids.buyers import seed_buyers
from toronto_bids.models import AgencyAward
from toronto_bids.store import db


# --- reporting new-vs-total distinctly (#177) ---------------------------------------------

def test_stored_line_reports_the_delta_beside_the_total():
    """A quiet night's `107 solicitations, 107 awards, 115 bids` used to be indistinguishable
    from a real one without cross-referencing the aggregate delta three lines down (#177)."""
    line = cli._stored_line("trca", {"solicitations": 107, "awards": 107, "bids": 115},
                            before=(88, 115), after=(88, 115))
    assert "107 awards (+0)" in line
    assert "115 bids (+0)" in line


def test_stored_line_shows_growth_when_there_is_any():
    line = cli._stored_line("trca", {"solicitations": 108, "awards": 108, "bids": 118},
                            before=(88, 115), after=(89, 118))
    assert "108 awards (+1)" in line
    assert "118 bids (+3)" in line


def test_stored_line_delta_is_the_real_row_count_not_the_loop_count():
    """`got["awards"]` counts REPORTS PROCESSED this call, not resulting rows — a report is
    upserted keyed on native_ref, so an amendment and its original can both increment the
    loop count while collapsing into the same row. Live-measured: a full EP reparse processed
    107 reports against a table holding 88 rows; `got["awards"] - before[0]` would have
    reported a fictitious +19. The delta must come from two real row-count queries, not from
    `got` at all (#177)."""
    line = cli._stored_line("ep", {"solicitations": 107, "awards": 107, "bids": 115},
                            before=(88, 115), after=(88, 115))
    assert "awards (+0)" in line


def test_stored_line_omits_bids_for_a_body_with_no_bid_table():
    """Zoo's store_zoo_reports returns no 'bids' key — the line must not fabricate one."""
    line = cli._stored_line("zoo", {"solicitations": 5, "awards": 5}, before=(5, 0), after=(5, 0))
    assert "bids" not in line


def test_source_row_counts_are_scoped_to_one_board(conn):
    ids = seed_buyers(conn)
    db.upsert_row(conn, AgencyAward(
        buyer_id=ids["trca"], native_ref="1", supplier_name_raw="X", award_amount=None,
        value_confidential=0, award_date=None, source="trca_board"), overwrite=True)
    db.upsert_row(conn, AgencyAward(
        buyer_id=ids["toronto-zoo"], native_ref="1", supplier_name_raw="Y", award_amount=None,
        value_confidential=0, award_date=None, source="zoo_board"), overwrite=True)
    assert cli._source_row_counts(conn, "trca_board") == (1, 0)
    assert cli._source_row_counts(conn, "zoo_board") == (1, 0)
    assert cli._source_row_counts(conn, "ep_board") == (0, 0)


# --- --only ep --portal must skip cleanly, not KeyError (#152) ---------------------------

def _agencies_env(monkeypatch, conn, tmp_path):
    """Offline `tb enrich-agencies`: no fetch/scrape, no real board parsing, no supplier
    rebuild work — isolates the --portal dispatch this test is actually about."""
    from toronto_bids import config
    monkeypatch.setattr(cli, "_open_db", lambda: conn)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cli, "_capture_agency_bodies", lambda *a, **k: [])
    from toronto_bids.linking import supplier
    monkeypatch.setattr(supplier, "build_supplier_dimension", lambda *a, **k: 0)


def test_only_ep_portal_completes_without_a_keyerror_or_a_portal_failure(
        conn, monkeypatch, tmp_path, capsys):
    """EP procures via Bonfire, not a bids&tenders portal (#134) — `{"trca":.., "zoo":..}[args.only]`
    raised an uncaught KeyError for `--only ep --portal`, caught by the surrounding try/except
    and recorded as a portal FAILURE for a body that was never supposed to have one."""
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
    """The body name ('zoo') and the portal's own slug ('toronto-zoo') differ — this mapping
    is the reason the lookup existed at all, and must survive the #152 fix unchanged."""
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
    """`--portal` without `--only` must keep capturing every enabled+permitted body — the
    #152 fix must only change the EP-specific dead end, not the default fan-out."""
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
    # TRCA raises; Zoo and EP still run and the failure is reported, not raised.
    ids = seed_buyers(conn)
    import toronto_bids.sources.trca_board as trca
    import toronto_bids.sources.zoo_board as zoo
    import toronto_bids.sources.ep_board as ep

    monkeypatch.setattr(trca, "store_trca_reports", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(zoo, "cached_zb_agendas", lambda *a, **k: {})
    monkeypatch.setattr(zoo, "store_zoo_reports", lambda *a, **k: {"solicitations": 0, "awards": 0})
    monkeypatch.setattr(ep, "cached_ep_agendas", lambda *a, **k: {})
    monkeypatch.setattr(ep, "store_ep_reports", lambda *a, **k: {"solicitations": 0, "awards": 0, "bids": 0})

    failures = cli._capture_agency_bodies(
        conn, ids, bodies=["trca", "zoo", "ep"],
        fetch=False, scrape=False, virtual_display=False, out=lambda _m: None)

    assert [name for name, _ in failures] == ["trca"]
