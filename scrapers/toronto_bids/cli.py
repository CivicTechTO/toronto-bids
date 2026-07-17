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
        help="Recover titles the City never published, from Bid Award Panel agendas "
             "and the legacy archive (offline unless --scrape)")
    p_titles.add_argument(
        "--scrape", action="store_true",
        help="Fetch Bid Award Panel agendas first (headed browser; ~10 min on a cold "
             "cache, seconds once cached). Without it, only agendas already on disk are used")
    p_titles.add_argument("--virtual-display", action="store_true",
                          help="Run the headed browser under Xvfb (implies --scrape's needs)")
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
    return parser


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
    """Fill titles the City never published. Offline by default; --scrape needs a browser.

    Both fills only ever touch a NULL title, so a title the City published always wins.
    They are ordered council-then-legacy for readable output, not for correctness: the
    precedence between the two is encoded in legacy_titles' query, so the result does not
    depend on which runs first.
    """
    from toronto_bids.sources.bid_award_panel import (
        _BA_REPORTS_WITHOUT_BIDS, _COMPOSITE_REPORTS, cached_agendas,
        download_reports, fill_titles_from_council,
        match_composite_titles, match_pre_ariba_titles, scrape_agendas,
        store_background_pdfs, store_bids, store_composite_awards, store_items)
    from toronto_bids.sources.legacy_titles import fill_titles_from_legacy

    conn = _open_db()
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM solicitation WHERE title IS NULL").fetchone()[0]

        if args.scrape:
            agendas = scrape_agendas(config.COUNCIL_AGENDAS_DIR,
                                     virtual_display=args.virtual_display,
                                     log=lambda m: print(m, flush=True))
        else:
            agendas = cached_agendas(config.COUNCIL_AGENDAS_DIR)
            if not agendas:
                print(f"No cached agendas in {config.COUNCIL_AGENDAS_DIR} — "
                      f"run with --scrape to fetch them (needs the 'council' extra).")

        if agendas:
            print(f"Bid Award Panel agendas: {len(agendas)}"
                  f" ({'scraped' if args.scrape else 'cached'})")
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
    """The whole unattended run: sync -> award summaries -> export -> tell Slack.

    Each step is isolated exactly as pipeline.run_source isolates a source: a failure is
    recorded and the steps behind it still run. In particular the EXPORT RUNS EVEN AFTER A
    PARTIAL SYNC — rows are committed per-source and never deleted, so partial data is still
    data, and discarding a good artifact over one bad feed would be the worse outcome.

    Exits non-zero if anything failed, so systemd marks the unit failed and the next run's
    Slack line is not the only record.
    """
    import time
    from pathlib import Path

    from toronto_bids import notify
    from toronto_bids.sources.award_summary import (
        download_award_summaries, store_award_summary_bids)

    started = time.monotonic()
    out = lambda m: print(m, flush=True)
    conn = _open_db()
    before = db.counts(conn)
    failures: list[tuple[str, str]] = []

    http = HttpClient()
    try:
        try:
            failures.extend(pipeline.sync(conn, http))
        except Exception as exc:
            failures.append(("sync", str(exc)))
        try:
            download_award_summaries(conn, http, log=out)
            store_award_summary_bids(conn, log=out)
        except Exception as exc:
            failures.append(("award_summary", str(exc)))
    finally:
        http.close()

    export_bytes = None
    try:
        written = export_json(conn, Path(config.DATA_DIR) / "export" / "bids.json")
        export_bytes = written.stat().st_size
    except Exception as exc:
        failures.append(("export", str(exc)))

    after = db.counts(conn)
    conn.close()

    text = notify.summarize(before, after, failures, len(pipeline.default_sources()),
                            export_bytes, time.monotonic() - started)
    print(text)
    for name, error in failures:
        print(f"FAILED  {name}: {error}", file=sys.stderr)
    notify.post(text, log=lambda m: print(m, file=sys.stderr))
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
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
