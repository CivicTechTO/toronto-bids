import argparse
import sys

from toronto_bids import __version__, config, pipeline
from toronto_bids.export.json_export import export_json
from toronto_bids.http import HttpClient
from toronto_bids.store import db


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tb", description="Toronto Bids scraper")
    parser.add_argument("--version", action="version", version=f"tb {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_sync = sub.add_parser("sync", help="Fetch all sources into the local store")
    p_sync.add_argument("--only", help="Comma-separated source names to run")

    sub.add_parser("status", help="Show row counts in the local store")

    p_export = sub.add_parser("export", help="Write the store to a nested JSON artifact")
    p_export.add_argument("--out", help="Output path (default: <DATA_DIR>/export/bids.json)")

    p_enrich = sub.add_parser("enrich-council",
                              help="OPT-IN: fetch council decisions + staff-report PDFs for suspended firms (headed browser)")
    p_enrich.add_argument("--virtual-display", action="store_true",
                          help="Run the headed browser under Xvfb (headless servers; needs Xvfb installed)")

    p_titles = sub.add_parser(
        "enrich-titles",
        help="Recover titles the City never published, from the cached Bid Award Panel "
             "agendas and the legacy archive (offline; the Panel was abolished 2025-10-01 so "
             "the cached corpus is final)")
    p_awards = sub.add_parser(
        "enrich-awards",
        help="Archive the Toronto Bids Portal's Award Summary Forms — the losing bidders, "
             "after the Bid Award Panel was abolished 2025-10-01 (#114). Offline unless "
             "--download")
    p_awards.add_argument(
        "--download", action="store_true",
        help="Fetch the forms first (plain HTTP, no browser; ~262 PDFs / ~64MB, resumable)")

    p_amounts = sub.add_parser(
        "amounts", help="Inspect the amount strings the parser refuses (#74)")
    p_amounts.add_argument(
        "action", choices=["unlabelled"],
        help="unlabelled: raw strings with no parse and no verdict in amount_labels.toml")

    p_ariba = sub.add_parser(
        "enrich-ariba-attachments",
        help="Archive the solicitation documents behind Ariba's Respond gate (#117). Only "
             "reaches currently-open postings — a recurring job, not a backfill.")
    p_ariba.add_argument(
        "--capture", action="store_true",
        help="Drive a headed, logged-in Chromium (council extra + scrapers/.env creds) through "
             "Respond -> Download Content -> Download Attachments for each open solicitation")
    p_ariba.add_argument(
        "--ingest", metavar="DIR",
        help="Index Doc*.zip bundles already sitting in DIR (e.g. a browser's download folder) "
             "into <DATA_DIR>/ariba/attachments/ and the manifest. No browser.")
    p_ariba.add_argument(
        "--headless", action="store_true",
        help="Run capture headless (Ariba's supplier UI is not Akamai-gated, unlike TMMIS; may "
             "still trip bot-detection — headed is the default)")
    p_ariba.add_argument(
        "--reindex", action="store_true",
        help="Rebuild the index from the bundles already on disk under <DATA_DIR>/ariba/"
             "attachments/ (offline, no browser). Needed once after the recursion change (#123).")
    p_ariba.add_argument(
        "--virtual-display", action="store_true",
        help="Run --capture's headed Chromium under Xvfb (headless servers; needs Xvfb "
             "installed). Ignored by the offline --ingest/--reindex modes.")

    p_titles.add_argument(
        "--reports", action="store_true",
        help="Download the 2009-2012 composite staff-report PDFs first, whose appendices carry "
             "awards the agendas of those years do not describe (#93). Plain HTTP and no "
             "browser, unlike --scrape; needs pdftotext. ~221 files / ~80MB, resumable")

    sub.add_parser(
        "nightly",
        help="Sync, archive new Award Summary Forms, export, and post a summary to Slack. "
             "What the systemd timer runs — see docs/superpowers/specs/"
             "2026-07-17-deployment-design.md")

    p_ag = sub.add_parser(
        "enrich-agencies",
        help="Capture agency/corporation procurement from board records (#135): TRCA "
             "(eSCRIBE, plain HTTP) and Toronto Zoo (ZB agendas on TMMIS). Offline by "
             "default — parses reports already on disk. NEVER touches the bids&tenders "
             "portal (gated on written permission, see docs/permissions/).")
    p_ag.add_argument("--only", choices=["zoo", "trca", "ep"],
                      help="Run one body instead of all")
    p_ag.add_argument("--fetch", action="store_true",
                      help="Plain-HTTP fetching first: TRCA eSCRIBE listings + report PDFs, "
                           "and legdocs PDFs for Zoo agendas already cached")
    p_ag.add_argument("--scrape", action="store_true",
                      help="Discover Zoo ZB agendas on TMMIS first (headed browser, "
                           "council extra; implies --fetch for the Zoo's PDFs)")
    p_ag.add_argument("--virtual-display", action="store_true",
                      help="Run --scrape's headed browser under Xvfb")
    p_ag.add_argument("--portal", action="store_true",
                      help="Capture bids&tenders portal listings for enabled+permitted bodies "
                           "(plain HTTP, rate-limited). Currently a no-op while portals are empty.")
    p_ag.add_argument("--record", action="store_true",
                      help="With --portal: also dump each raw JSON record under "
                           "<DATA_DIR>/agencies/portal_recordings/ to seed parser fixtures.")
    return parser


def _is_first_of_month() -> bool:
    """The monthly-council gate for the nightly (a test seam)."""
    from datetime import date
    return date.today().day == 1


def _run_step(steps: list, failures: list, name: str, fn) -> None:
    """Run one nightly step, timing it and recording a step record. On failure the error is
    ALSO appended to `failures` — the authoritative list the exit code and the report's Failures
    section read — so the exit-code contract is untouched while the Steps section gains status +
    timing. `fn` returns a short detail string (or None)."""
    import time
    t = time.monotonic()
    try:
        detail = fn()
        steps.append({"name": name, "status": "ok", "detail": detail or "",
                      "seconds": time.monotonic() - t, "error": None})
    except Exception as exc:  # isolation: never propagates
        steps.append({"name": name, "status": "fail", "detail": "",
                      "seconds": time.monotonic() - t, "error": str(exc)})
        failures.append((name, str(exc)))


def _report_sources(conn, after_id: int) -> list:
    """This-run sync_run rows for the DATA fetch sources only — the report's Sources section.

    sync_run also records the schema-drift validator (`schema_check`, which writes 0 rows by
    nature) and the post-source linking passes (`title_cleanup`, `ariba_bridge`,
    `amount_backfill`, `amount_labels`, `supplier_dimension`). Those are not fetches — a pass
    touching 0 rows is normal — so including them would falsely ⚠-flag them as broken every
    single night. Keep only the real City feeds; a genuine `schema_check` failure still surfaces
    in the Failures section via pipeline.sync's return.
    """
    names = {s.name for s in pipeline.default_sources()} - {"schema_check"}
    return [r for r in db.sync_runs_since(conn, after_id) if r["source"] in names]


def _open_db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = db.connect(config.DB_PATH)
    db.init_db(conn)
    return conn


def _cmd_sync(args) -> int:
    conn = _open_db()
    http = HttpClient()
    only = [n.strip() for n in args.only.split(",")] if args.only else None
    if only is not None:
        known = {s.name for s in pipeline.default_sources()}
        unknown = [name for name in only if name not in known]
        if unknown:
            print(f"Warning: unknown source name(s): {', '.join(unknown)}")
    try:
        failures = pipeline.sync(conn, http, only=only)
        counts = db.counts(conn)
        print("Row counts:", ", ".join(f"{k}={v}" for k, v in counts.items()))
        for name, error in failures:
            print(f"FAILED  {name}: {error}", file=sys.stderr)
        print("Sync complete" if not failures
              else f"Sync finished with {len(failures)} failed source(s)")
    finally:
        http.close()
        conn.close()
    # Non-zero so a human or a cron job notices; a silent zero-row run is the bug (#18).
    return 1 if failures else 0


def _cmd_status(args) -> int:
    conn = _open_db()
    for table, n in db.counts(conn).items():
        print(f"{table:16s} {n}")
    runs = db.last_runs(conn)
    if runs:
        print("\nLast run per source:")
        for r in runs:
            line = f"  {r['source']:22s} {r['status']:8s} {r['started_at']}  rows={r['rows_upserted']}"
            print(line + (f"\n    error: {r['error']}" if r["error"] else ""))
    conn.close()
    return 0


def _cmd_export(args) -> int:
    from pathlib import Path

    conn = _open_db()
    try:
        out_path = Path(args.out) if args.out else config.DATA_DIR / "export" / "bids.json"
        written = export_json(conn, out_path)
        counts = db.counts(conn)
        print(f"Exported {counts['solicitation']} solicitations to {written}")
    finally:
        conn.close()
    return 0


def _cmd_enrich_council(args) -> int:
    from functools import partial

    from toronto_bids.sources.council import enrich_council, fetch_agenda_item

    conn = _open_db()
    http = HttpClient()
    fetch = partial(fetch_agenda_item, virtual_display=args.virtual_display)
    try:
        n = enrich_council(conn, http, fetch=fetch)
        counts = db.counts(conn)
        print(f"Enriched {n} council items; background_pdf={counts['background_pdf']}")
    finally:
        http.close()
        conn.close()
    return 0


def _cmd_enrich_titles(args) -> int:
    """Fill titles the City never published. Offline: the Bid Award Panel was abolished
    2025-10-01, so the cached agenda corpus is final and there is no scrape path.

    Both fills only ever touch a NULL title, so a title the City published always wins.
    They are ordered council-then-legacy for readable output, not for correctness: the
    precedence between the two is encoded in legacy_titles' query, so the result does not
    depend on which runs first.
    """
    from toronto_bids.sources.bid_award_panel import (
        _BA_REPORTS_WITHOUT_BIDS, _COMPOSITE_REPORTS, cached_agendas,
        download_reports, fill_titles_from_council,
        match_composite_titles, match_pre_ariba_solicitations, match_pre_ariba_titles,
        store_background_pdfs, store_bids, store_composite_awards, store_items)
    from toronto_bids.sources.legacy_titles import fill_titles_from_legacy

    conn = _open_db()
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM solicitation WHERE title IS NULL").fetchone()[0]

        agendas = cached_agendas(config.COUNCIL_AGENDAS_DIR)
        if not agendas:
            print(f"No cached agendas in {config.COUNCIL_AGENDAS_DIR} — download the "
                  f"council-agendas archive from the data release and unpack it there "
                  f"(deploy/README.md).")

        if agendas:
            print(f"Bid Award Panel agendas: {len(agendas)} (cached)")
            print(f"  council items stored: {store_items(conn, agendas)}")
            # The same cached pages are the staff-report index spec §2.3 says does not
            # exist, so index them here rather than make anyone walk the files twice.
            print(f"  staff reports indexed: {store_background_pdfs(conn, agendas)}")
            # The same pages tabulate every bid, including the losers that spec
            # §2.5.2 calls unrecoverable (#84).
            print(f"  bids extracted       : {store_bids(conn, agendas)}")
            print(f"  titles from council : {fill_titles_from_council(conn)}")
            # Pre-Ariba items name no document number, so they are matched on
            # (supplier, award value) instead (#77).
            print(f"  titles pre-Ariba    : {match_pre_ariba_titles(conn, agendas)}")
            print(f"  bids linked pre-Ariba: {match_pre_ariba_solicitations(conn, agendas)}")

        # 2009-2012 agendas describe nothing ("Composite Report"); their staff-report
        # appendices carry the awards, and feed them to the same join (#93).
        if args.reports:
            http = HttpClient()
            try:
                out = lambda m: print(m, flush=True)
                n = download_reports(conn, http, _COMPOSITE_REPORTS,
                                     "composite reports", log=out)
                print(f"  composite reports downloaded: {n}")
                # The only thing the PDFs can still add: BA items whose agenda tabulates no
                # bids (#83). Everything else the panel handled has its bids from the agenda.
                n = download_reports(conn, http, _BA_REPORTS_WITHOUT_BIDS,
                                     "BA reports without a bid table", log=out)
                print(f"  BA reports downloaded       : {n}")
            finally:
                http.close()
        print(f"  titles composite    : {match_composite_titles(conn)}")
        # Not a linking pass: for 2009-2011 the City's feed publishes 13 awards against the
        # 799 in these reports, so this is the archive reaching back past the feed (#96).
        print(f"  composite awards    : {store_composite_awards(conn)}")

        n_legacy = fill_titles_from_legacy(conn, config.LEGACY_ARIBA_DIR)
        print(f"  titles from legacy  : {n_legacy}"
              if n_legacy or config.LEGACY_ARIBA_DIR.is_dir()
              else f"  legacy archive absent ({config.LEGACY_ARIBA_DIR}) — skipped")

        after = conn.execute(
            "SELECT COUNT(*) FROM solicitation WHERE title IS NULL").fetchone()[0]
        print(f"\nTitle-less solicitations: {before} -> {after}  ({before - after} named)")
        for source, n in conn.execute(
                "SELECT COALESCE(title_source, 'odata (City feed)'), COUNT(*) "
                "FROM solicitation WHERE title IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"):
            print(f"  {source:<20} {n:>5}")
    finally:
        conn.close()
    return 0


def _cmd_enrich_awards(args) -> int:
    """Archive and parse the Award Summary Forms (#114).

    The Bid Award Panel was abolished on 2025-10-01, so `enrich-titles --scrape` will never
    find another agenda: 891 is the final corpus. This is where the losing bidders come from
    now. Offline by default — it parses forms already on disk, exactly as enrich-titles does.
    """
    from toronto_bids.sources.award_summary import (
        download_award_summaries, store_award_summary_bids)

    conn = _open_db()
    try:
        before = conn.execute("SELECT COUNT(*) FROM bid").fetchone()[0]
        if args.download:
            http = HttpClient()
            try:
                out = lambda m: print(m, flush=True)
                print(f"  award summary forms archived: "
                      f"{download_award_summaries(conn, http, log=out)}")
            finally:
                http.close()
        else:
            n = conn.execute("SELECT COUNT(*) FROM background_pdf "
                             "WHERE kind='award_summary' AND text IS NOT NULL").fetchone()[0]
            if not n:
                print("No Award Summary Forms on disk — run with --download to fetch them "
                      "(plain HTTP, no browser).")
        print(f"  bids from award summaries   : "
              f"{store_award_summary_bids(conn, log=lambda m: print(m, flush=True))}")
        after = conn.execute("SELECT COUNT(*) FROM bid").fetchone()[0]
        print(f"\nBids: {before} -> {after}  ({after - before} new)")
        for r in conn.execute("SELECT source, COUNT(*) n FROM bid GROUP BY 1 ORDER BY 2 DESC"):
            print(f"  {r['source']:<22} {r['n']:>6}")
    finally:
        conn.close()
    return 0


def _cmd_enrich_ariba_attachments(args) -> int:
    """Archive the documents behind Ariba's Respond gate (#117).

    Two entry points on one manifest: --capture drives the browser; --ingest indexes bundles a
    human already downloaded. Both land in the same ariba_attachment index and the same
    <DATA_DIR>/ariba/attachments/ store. Nothing is surfaced in the export yet — archive now,
    publish later.
    """
    from toronto_bids.sources import ariba_attachments as aa

    conn = _open_db()
    out = lambda m: print(m, flush=True)
    try:
        before = conn.execute("SELECT COUNT(*) FROM ariba_attachment").fetchone()[0]
        if args.reindex:
            print("Reindexing bundles on disk (recursive):")
            print(f"  bundles reindexed: {aa.reindex_bundles(conn, log=out)}")
        elif args.ingest:
            print(f"Indexing bundles in {args.ingest}:")
            print(f"  bundles ingested: {aa.ingest_downloads(conn, args.ingest, log=out)}")
        elif args.capture:
            print("Capturing open-solicitation attachment bundles (headed browser):")
            n = aa.capture_attachments(conn, log=out, headless=args.headless,
                                       virtual_display=args.virtual_display)
            print(f"  bundles captured: {n}")
        else:
            open_n = len(aa.open_solicitation_events(conn))
            docs = conn.execute(
                "SELECT COUNT(DISTINCT document_number) FROM ariba_attachment").fetchone()[0]
            print(f"Open solicitations with a modern Ariba link: {open_n}")
            print(f"Solicitations archived so far             : {docs}")
            print("\nRun with --capture to drive the browser, or --ingest DIR to index bundles "
                  "already downloaded.")
            return 0
        after = conn.execute("SELECT COUNT(*) FROM ariba_attachment").fetchone()[0]
        docs = conn.execute(
            "SELECT COUNT(DISTINCT document_number) FROM ariba_attachment").fetchone()[0]
        print(f"\nIndexed files: {before} -> {after}  ({after - before} new)  "
              f"across {docs} solicitation(s)")
    finally:
        conn.close()
    return 0


def _cmd_amounts(args) -> int:
    """Surface amount strings nobody has ruled on yet.

    The discovery half of #74: the labels file handles the 35 known strings, and this is what
    stops the trickle from future syncs sitting silent until someone happens to look.
    """
    from toronto_bids.linking.amount_labels import load_labels, unlabelled_amounts

    conn = _open_db()
    try:
        pending = unlabelled_amounts(conn)
        if not pending:
            print(f"No unlabelled amounts. ({len(load_labels())} labels in "
                  f"toronto_bids/data/amount_labels.toml cover every string the parser "
                  f"refuses.)")
            return 0
        print(f"{len(pending)} amount string(s) with no parse and no label:\n")
        print(f"  {'table':16s} {'rows':>5s}  raw")
        for row in pending:
            print(f"  {row['table']:16s} {row['rows']:5d}  {row['raw']!r}")
        print("\nAdd a verdict for each to toronto_bids/data/amount_labels.toml "
              "(amount / not_an_amount / corrupt / unknown / not_an_award).")
    finally:
        conn.close()
    # Non-zero so a human or CI notices the queue is non-empty, as `tb sync` does for
    # failures. An unreviewed amount is a known gap, not an error — but a silent one is worse.
    return 1


def _cmd_nightly(args) -> int:
    """The whole unattended run: sync -> award summaries -> portal -> ariba attachments ->
    agency board reports -> (monthly) council -> supplier rebuild -> export -> tell Slack.

    Each step is isolated exactly as pipeline.run_source isolates a source: a failure is
    recorded and the steps behind it still run. In particular the EXPORT RUNS EVEN AFTER A
    PARTIAL SYNC — rows are committed per-source and never deleted, so partial data is still
    data, and discarding a good artifact over one bad feed would be the worse outcome. That
    reasoning applies just as much to opening the DB and building the HTTP client: those used
    to sit outside every try, so a dead disk or a network hiccup left `_cmd_nightly` uncaught
    and notify.post never fired — silence, in the one case a nightly line exists to catch.
    `conn` and `http` are opened defensively for exactly that reason, and the export runs off
    `conn` alone — a failed HttpClient must not cost us a good artifact.

    Exits non-zero if anything failed, so systemd marks the unit failed and the next run's
    Slack line is not the only record. Never raises: every step, including closing what we
    opened, is caught so notify.summarize/post always run.
    """
    import time
    from pathlib import Path

    from toronto_bids import notify
    from toronto_bids.sources.award_summary import (
        download_award_summaries, store_award_summary_bids)

    started = time.monotonic()
    out = lambda m: print(m, flush=True)
    failures: list[tuple[str, str]] = []
    before: dict = {}
    after: dict = {}
    export_bytes = None
    conn = None
    steps: list[dict] = []
    sources: list[dict] = []

    try:
        conn = _open_db()
    except Exception as exc:
        failures.append(("open_db", str(exc)))

    # Separate from the open above: if `conn` opened fine but this raises, blaming "open_db"
    # would send the reader to the wrong system, and leaving `before` at {} would let a
    # transient failure that clears by the `after` count fabricate a delta against zero.
    if conn is not None:
        try:
            before = db.counts(conn)
        except Exception as exc:
            failures.append(("counts", str(exc)))

    if conn is not None:
        http = None
        try:
            http = HttpClient()
        except Exception as exc:
            failures.append(("http_client", str(exc)))

        if http is not None:
            try:
                try:
                    sync_cutoff = conn.execute(
                        "SELECT COALESCE(MAX(id), 0) FROM sync_run").fetchone()[0]
                except Exception:
                    sync_cutoff = 0

                def _sync():
                    src_failures = pipeline.sync(conn, http)
                    failures.extend(src_failures)
                    n = len(pipeline.default_sources())
                    return f"{n} sources"
                _run_step(steps, failures, "sync", _sync)

                try:
                    sources.extend(_report_sources(conn, sync_cutoff))
                except Exception as exc:
                    failures.append(("sync_detail", str(exc)))

                def _awards():
                    download_award_summaries(conn, http, log=out)
                    return f"{store_award_summary_bids(conn, log=out)} bids stored"
                _run_step(steps, failures, "award summaries", _awards)

                def _portal():
                    from toronto_bids.sources.bids_tenders import run_portal_capture
                    res = run_portal_capture(conn, log=out)
                    for slug, v in res.items():
                        if isinstance(v, str) and v.startswith("FAILED"):
                            failures.append((f"portal:{slug}", v))
                    total = sum(v for v in res.values() if isinstance(v, int))
                    return f"{total} listings" if total else "no open bids"
                _run_step(steps, failures, "portal", _portal)

                def _ariba():
                    from toronto_bids.sources import ariba_attachments as aa
                    n = aa.capture_attachments(conn, log=out, virtual_display=True)
                    return f"+{n} bundles"
                _run_step(steps, failures, "ariba attachments", _ariba)

                def _agencies():
                    from toronto_bids.buyers import seed_buyers
                    a0 = db.counts(conn)
                    ids = seed_buyers(conn)
                    failures.extend(_capture_agency_bodies(
                        conn, ids, bodies=["trca", "zoo", "ep"],
                        fetch=True, scrape=True, virtual_display=True, out=out))
                    a1 = db.counts(conn)
                    da = a1["agency_award"] - a0["agency_award"]
                    db_ = a1["agency_bid"] - a0["agency_bid"]
                    return f"+{da} awards, +{db_} bids"
                _run_step(steps, failures, "agencies", _agencies)

                if _is_first_of_month():
                    def _council():
                        from functools import partial
                        from toronto_bids.sources.council import (
                            enrich_council, fetch_agenda_item)
                        fetch = partial(fetch_agenda_item, virtual_display=True)
                        n = enrich_council(conn, http, fetch=fetch)
                        return f"{n} items"
                    _run_step(steps, failures, "council", _council)
                else:
                    steps.append({"name": "council", "status": "skip",
                                  "detail": "not the 1st", "seconds": 0.0, "error": None})
            finally:
                try:
                    http.close()
                except Exception as exc:
                    failures.append(("http_close", str(exc)))

        def _supplier():
            from toronto_bids.linking.supplier import build_supplier_dimension
            return f"{build_supplier_dimension(conn)} suppliers"
        _run_step(steps, failures, "supplier rebuild", _supplier)

        def _export():
            nonlocal export_bytes
            written = export_json(conn, Path(config.DATA_DIR) / "export" / "bids.json")
            export_bytes = written.stat().st_size
            return f"{export_bytes / 1_048_576:.1f} MiB"
        _run_step(steps, failures, "export", _export)

        try:
            after = db.counts(conn)
        except Exception as exc:
            failures.append(("counts", str(exc)))

        try:
            conn.close()
        except Exception as exc:
            failures.append(("conn_close", str(exc)))

    report = {
        "ok": not failures,
        "steps": steps,
        "sources": sources,
        "before": before,
        "after": after,
        "failures": failures,
        "export_bytes": export_bytes,
        "elapsed_s": time.monotonic() - started,
    }
    text = notify.summarize(report)
    print(text)
    for name, error in failures:
        print(f"FAILED  {name}: {error}", file=sys.stderr)
    notify.post(text, log=lambda m: print(m, file=sys.stderr))
    return 1 if failures else 0


def _capture_agency_bodies(conn, ids, *, bodies, fetch, scrape, virtual_display, out):
    """Capture TRCA/Zoo/EP board-report awards+bids, each body isolated. Returns failures.

    Shared by `tb enrich-agencies` and `tb nightly`. TRCA is plain HTTP (eSCRIBE); Zoo and EP
    need a headed browser for TMMIS discovery, so `scrape`/`virtual_display` apply to them.
    Does not run the portal step or the supplier rebuild — the caller owns those.
    """
    failures: list[tuple[str, str]] = []

    if "trca" in bodies:
        try:
            from toronto_bids.sources.trca_board import download_reports, store_trca_reports
            if fetch:
                http = HttpClient()
                try:
                    print(f"  trca reports fetched : {download_reports(conn, http, log=out)}")
                finally:
                    http.close()
            got = store_trca_reports(conn, ids["trca"])
            print(f"  trca stored          : {got['solicitations']} solicitations, "
                  f"{got['awards']} awards, {got['bids']} bids")
        except Exception as exc:
            failures.append(("trca", str(exc)))

    if "zoo" in bodies:
        try:
            from toronto_bids.sources.zoo_board import (
                cached_zb_agendas, download_zoo_reports, scrape_zb_agendas, store_zoo_reports)
            agendas = (scrape_zb_agendas(virtual_display=virtual_display, log=out)
                       if scrape else cached_zb_agendas())
            print(f"  zoo ZB agendas       : {len(agendas)}"
                  f" ({'scraped' if scrape else 'cached'})")
            if agendas and (fetch or scrape):
                http = HttpClient()
                try:
                    print(f"  zoo reports fetched  : "
                          f"{download_zoo_reports(conn, http, agendas, log=out)}")
                finally:
                    http.close()
            got = store_zoo_reports(conn, ids["toronto-zoo"])
            print(f"  zoo stored           : {got['solicitations']} solicitations, "
                  f"{got['awards']} awards")
        except Exception as exc:
            failures.append(("zoo", str(exc)))

    if "ep" in bodies:
        try:
            from toronto_bids.sources.ep_board import (
                cached_ep_agendas, download_ep_reports, scrape_ep_agendas, store_ep_reports)
            agendas = (scrape_ep_agendas(virtual_display=virtual_display, log=out)
                       if scrape else cached_ep_agendas())
            print(f"  ep EP agendas        : {len(agendas)}"
                  f" ({'scraped' if scrape else 'cached'})")
            if agendas and (fetch or scrape):
                http = HttpClient()
                try:
                    print(f"  ep reports fetched   : "
                          f"{download_ep_reports(conn, http, agendas, log=out)}")
                finally:
                    http.close()
            got = store_ep_reports(conn, ids["exhibition-place"])
            print(f"  ep stored            : {got['solicitations']} solicitations, "
                  f"{got['awards']} awards, {got['bids']} bids")
        except Exception as exc:
            failures.append(("ep", str(exc)))

    return failures


def _cmd_enrich_agencies(args) -> int:
    from toronto_bids.buyers import seed_buyers
    from toronto_bids.linking.supplier import build_supplier_dimension

    conn = _open_db()
    out = lambda m: print(m, flush=True)
    failures: list[tuple[str, str]] = []
    try:
        ids = seed_buyers(conn)
        bodies = [args.only] if args.only else ["trca", "zoo", "ep"]

        failures.extend(_capture_agency_bodies(
            conn, ids, bodies=bodies, fetch=args.fetch, scrape=args.scrape,
            virtual_display=args.virtual_display, out=out))

        if args.portal:
            try:
                from toronto_bids.sources.bids_tenders import run_portal_capture
                only = None if not args.only else {"trca": "trca", "zoo": "toronto-zoo"}[args.only]
                res = run_portal_capture(conn, record=args.record,
                                         only={only} if only else None, log=out)
                print(f"  portal listings      : {res}")
                for slug, v in res.items():
                    if isinstance(v, str) and v.startswith("FAILED"):
                        failures.append((f"portal:{slug}", v))
            except Exception as exc:
                failures.append(("portal", str(exc)))

        try:
            print(f"  suppliers            : {build_supplier_dimension(conn)}")
        except Exception as exc:
            failures.append(("supplier_linking", str(exc)))
    finally:
        conn.close()
    for name, error in failures:
        print(f"FAILED  {name}: {error}", file=sys.stderr)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "sync":
        return _cmd_sync(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "export":
        return _cmd_export(args)
    if args.command == "nightly":
        return _cmd_nightly(args)
    if args.command == "enrich-council":
        return _cmd_enrich_council(args)
    if args.command == "enrich-titles":
        return _cmd_enrich_titles(args)
    if args.command == "enrich-awards":
        return _cmd_enrich_awards(args)
    if args.command == "amounts":
        return _cmd_amounts(args)
    if args.command == "enrich-ariba-attachments":
        return _cmd_enrich_ariba_attachments(args)
    if args.command == "enrich-agencies":
        return _cmd_enrich_agencies(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
