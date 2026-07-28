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
