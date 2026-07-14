import argparse

from toronto_bids import __version__, config, pipeline
from toronto_bids.http import HttpClient
from toronto_bids.store import db


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tb", description="Toronto Bids scraper")
    parser.add_argument("--version", action="version", version=f"tb {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_sync = sub.add_parser("sync", help="Fetch all sources into the local store")
    p_sync.add_argument("--only", help="Comma-separated source names to run")

    sub.add_parser("status", help="Show row counts in the local store")
    return parser


def _open_db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = db.connect(config.DB_PATH)
    db.init_db(conn)
    return conn


def _cmd_sync(args) -> int:
    conn = _open_db()
    http = HttpClient()
    only = args.only.split(",") if args.only else None
    try:
        pipeline.sync(conn, http, only=only)
    finally:
        http.close()
    counts = db.counts(conn)
    print("Sync complete:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    conn.close()
    return 0


def _cmd_status(args) -> int:
    conn = _open_db()
    for table, n in db.counts(conn).items():
        print(f"{table:16s} {n}")
    conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "sync":
        return _cmd_sync(args)
    if args.command == "status":
        return _cmd_status(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
