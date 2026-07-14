from toronto_bids.sources.ckan import CkanSource
from toronto_bids.sources.odata import ODataNonCompetitiveSource, ODataSolicitationSource
from toronto_bids.store import db
from toronto_bids import config


def default_sources():
    """OData spine first (overwrite=True), then CKAN backfill (overwrite=False)."""
    return [
        ODataSolicitationSource(),
        ODataNonCompetitiveSource(),
        CkanSource(name="ckan_awarded", slug=config.CKAN_AWARDED_SLUG, kind="awarded"),
        CkanSource(name="ckan_open", slug=config.CKAN_OPEN_SLUG, kind="open"),
        CkanSource(name="ckan_noncomp", slug=config.CKAN_NONCOMP_SLUG, kind="noncompetitive"),
    ]


def run_source(conn, http, source):
    """Run one source; record a sync_run. Never raises — returns (fetched, upserted)."""
    run_id = db.start_sync_run(conn, source.name)
    fetched = upserted = 0
    try:
        for raw in source.fetch(http):
            fetched += 1
            for row in source.normalize(raw):
                db.upsert_row(conn, row, overwrite=source.overwrite)
                upserted += 1
        conn.commit()
        db.finish_sync_run(conn, run_id, status="ok",
                           rows_fetched=fetched, rows_upserted=upserted)
    except Exception as exc:  # per-source isolation
        conn.commit()
        db.finish_sync_run(conn, run_id, status="failed",
                           rows_fetched=fetched, rows_upserted=upserted, error=str(exc))
    return fetched, upserted


def sync(conn, http, sources=None, only=None) -> None:
    sources = sources if sources is not None else default_sources()
    if only:
        wanted = set(only)
        sources = [s for s in sources if s.name in wanted]
    for source in sources:
        run_source(conn, http, source)
