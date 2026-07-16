from toronto_bids.sources.ariba import AribaDiscoverySource
from toronto_bids.sources.ckan import CkanSource
from toronto_bids.sources.odata import ODataNonCompetitiveSource, ODataSolicitationSource
from toronto_bids.sources.schema_check import SchemaCheckSource
from toronto_bids.sources.suspended_firms import SuspendedFirmsSource
from toronto_bids.store import db
from toronto_bids import config
from toronto_bids.linking.ariba import bridge_postings_to_spine
from toronto_bids.linking.supplier import build_supplier_dimension
from toronto_bids.title import clear_placeholder_titles


def default_sources():
    """OData spine first (overwrite=True), then CKAN backfill (overwrite=False).

    schema_check leads: it reports feed drift without blocking the sources behind it
    (per-source isolation), so drift is loud but never costs us a run's data.
    """
    return [
        SchemaCheckSource(),
        ODataSolicitationSource(),
        ODataNonCompetitiveSource(),
        CkanSource(name="ckan_awarded", slug=config.CKAN_AWARDED_SLUG, kind="awarded"),
        CkanSource(name="ckan_open", slug=config.CKAN_OPEN_SLUG, kind="open"),
        CkanSource(name="ckan_noncomp", slug=config.CKAN_NONCOMP_SLUG, kind="noncompetitive"),
        # No spine covers the forward-looking pipeline, so CKAN is authoritative for it
        # and it overwrites (#69).
        CkanSource(name="ckan_pipeline", slug=config.CKAN_PIPELINE_SLUG,
                   kind="capital_pipeline", overwrite=True),
        AribaDiscoverySource(),
        SuspendedFirmsSource(),
    ]


def run_source(conn, http, source):
    """Run one source; record a sync_run. Never raises — returns (fetched, upserted, error).

    error is None on success. Callers are responsible for surfacing it: this is the only
    place a source's exception is seen, so silence here means silence everywhere.
    """
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
        return fetched, upserted, None
    except Exception as exc:  # per-source isolation
        conn.commit()
        db.finish_sync_run(conn, run_id, status="failed",
                           rows_fetched=fetched, rows_upserted=upserted, error=str(exc))
        return fetched, upserted, str(exc)


def sync(conn, http, sources=None, only=None) -> list[tuple[str, str]]:
    """Run every source in isolation. Returns [(source_name, error)] for those that failed."""
    sources = sources if sources is not None else default_sources()
    if only is not None:
        wanted = set(only)
        sources = [s for s in sources if s.name in wanted]
    failures = []
    for source in sources:
        *_, error = run_source(conn, http, source)
        if error:
            failures.append((source.name, error))
    # Post-source passes run after every source, and regardless of --only: they read whatever
    # is in the store, so a partial sync still leaves it internally consistent.
    # title_cleanup leads: it clears placeholder titles COALESCE would otherwise preserve
    # forever (#70), and the passes behind it read titles.
    for name, link in (("title_cleanup", clear_placeholder_titles),
                       ("ariba_bridge", bridge_postings_to_spine),
                       ("supplier_dimension", build_supplier_dimension)):
        error = _run_linking_pass(conn, name, link)
        if error:
            failures.append((name, error))
    return failures


def _run_linking_pass(conn, name, link) -> str | None:
    """Run one post-source pass. Isolated like a source: never raises out of sync."""
    run_id = db.start_sync_run(conn, name)
    try:
        n = link(conn)
        db.finish_sync_run(conn, run_id, status="ok", rows_fetched=n, rows_upserted=n)
        return None
    except Exception as exc:
        conn.rollback()
        db.finish_sync_run(conn, run_id, status="failed", error=str(exc))
        return str(exc)
