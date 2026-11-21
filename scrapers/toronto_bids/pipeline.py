from toronto_bids.sources.ariba import AribaDiscoverySource
from toronto_bids.sources.ckan import CkanSource
from toronto_bids.sources.odata import ODataNonCompetitiveSource, ODataSolicitationSource
from toronto_bids.sources.schema_check import SchemaCheckSource
from toronto_bids.sources.suspended_firms import SuspendedFirmsSource
from toronto_bids.store import db
from toronto_bids import config
from toronto_bids.linking.ariba import bridge_postings_to_spine
from toronto_bids.linking.amount_labels import apply_amount_labels, backfill_numeric_amounts
from toronto_bids.linking.supplier import build_supplier_dimension
from toronto_bids.title import clear_placeholder_titles
from toronto_bids.linking.procurement import resolve_procurements


def default_sources():
    """Return sources in run order: schema_check, OData (overwrite), CKAN (backfill), Ariba, suspended firms."""
    return [
        SchemaCheckSource(),
        ODataSolicitationSource(),
        ODataNonCompetitiveSource(),
        CkanSource(name="ckan_awarded", slug=config.CKAN_AWARDED_SLUG, kind="awarded"),
        CkanSource(name="ckan_open", slug=config.CKAN_OPEN_SLUG, kind="open"),
        CkanSource(name="ckan_noncomp", slug=config.CKAN_NONCOMP_SLUG, kind="noncompetitive"),
        # CKAN is the only source for capital_pipeline, so it overwrites.
        CkanSource(name="ckan_pipeline", slug=config.CKAN_PIPELINE_SLUG,
                   kind="capital_pipeline", overwrite=True),
        AribaDiscoverySource(),
        SuspendedFirmsSource(),
    ]


def linking_passes():
    """Post-source passes in required run order.

    title_cleanup runs before title readers; amount_backfill runs before amount_labels so
    *_numeric IS NULL reflects current parsing.
    """
    return (("title_cleanup", clear_placeholder_titles),
            ("ariba_bridge", bridge_postings_to_spine),
            ("amount_backfill", backfill_numeric_amounts),
            ("amount_labels", apply_amount_labels),
            ("supplier_dimension", build_supplier_dimension),
            ("procurement_identity", resolve_procurements))


def run_source(conn, http, source):
    """Run one source and record a sync_run row. Catches errors and returns (fetched, upserted, error)."""
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
    except Exception as exc:
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
    # Run linking passes even under --only so the store stays consistent.
    for name, link in linking_passes():
        error = _run_linking_pass(conn, name, link)
        if error:
            failures.append((name, error))
    return failures


def _run_linking_pass(conn, name, link) -> str | None:
    """Run one post-source pass in isolation and record a sync_run row."""
    run_id = db.start_sync_run(conn, name)
    try:
        n = link(conn)
        db.finish_sync_run(conn, run_id, status="ok", rows_fetched=n, rows_upserted=n)
        return None
    except Exception as exc:
        conn.rollback()
        db.finish_sync_run(conn, run_id, status="failed", error=str(exc))
        return str(exc)
