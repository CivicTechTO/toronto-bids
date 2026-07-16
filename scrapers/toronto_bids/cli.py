import argparse

from toronto_bids import __version__, config, pipeline
from toronto_bids.export.json_export import JsonExporter
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
        pipeline.sync(conn, http, only=only)
        counts = db.counts(conn)
        print("Sync complete:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    finally:
        http.close()
        conn.close()
    return 0


def _cmd_status(args) -> int:
    conn = _open_db()
    for table, n in db.counts(conn).items():
        print(f"{table:16s} {n}")
    conn.close()
    return 0


def _cmd_export(args) -> int:
    from pathlib import Path

    conn = _open_db()
    try:
        out_path = Path(args.out) if args.out else config.DATA_DIR / "export" / "bids.json"
        written = JsonExporter().export(conn, out_path)
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "sync":
        return _cmd_sync(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "export":
        return _cmd_export(args)
    if args.command == "enrich-council":
        return _cmd_enrich_council(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
