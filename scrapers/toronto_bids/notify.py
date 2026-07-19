"""Operational notification for the nightly job (deployment spec §3.4).

The v1 design dropped Slack notifications from scope ("these can wrap the CLI later if
wanted"); this is that wrapper, and it carries no data — only whether the run worked.

The split is deliberate: `summarize` is pure, so the entire message is tested offline against
fixture counts, and `post` is one HTTP call with nothing to get wrong.
"""
import os

import httpx

_SLACK_TIMEOUT = 15.0


def _elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


_STEP_ICON = {"ok": "✅", "fail": "❌", "skip": "➖"}

# Nicer labels for the Growth section; unlisted tables fall back to the raw key.
_LABELS = {
    "solicitation": "solicitations", "award": "awards", "bid": "bids", "supplier": "suppliers",
    "noncompetitive": "noncompetitive", "ariba_posting": "ariba postings",
    "suspended_firm": "suspended firms", "capital_project": "capital projects",
    "council_item": "council items", "background_pdf": "background pdfs",
    "composite_award": "composite awards", "agency_solicitation": "agency solicitations",
    "agency_award": "agency awards", "agency_bid": "agency bids",
    "ariba_attachment": "ariba files",
}
# Housekeeping tables whose growth is noise in a report about the archive.
_GROWTH_SKIP = {"sync_run", "buyer"}


def _growth(before: dict, after: dict) -> list[str]:
    """'solicitations 7,446 (+12)' for every table that grew. Empty `before` (the count step
    failed) yields nothing — a delta against a zero nobody measured is a fabricated number."""
    if not before:
        return []
    out = []
    for key, now in after.items():
        if key in _GROWTH_SKIP:
            continue
        delta = now - before.get(key, 0)
        if delta:
            out.append(f"{_LABELS.get(key, key)} {now:,} ({delta:+,})")
    return out


def summarize(report: dict) -> str:
    """The nightly message — multi-section Slack mrkdwn. Pure and total over a partial report:
    a missing/empty section is simply omitted, never a KeyError, because the report itself must
    never be the thing that fails the run it exists to make visible.
    """
    before = report.get("before", {})
    after = report.get("after", {})
    failures = report.get("failures", [])
    steps = report.get("steps", [])
    sources = report.get("sources", [])
    export_bytes = report.get("export_bytes")
    elapsed_s = report.get("elapsed_s", 0.0)
    ok = report.get("ok", not failures)

    lines = [f"*{'✅' if ok else '❌'} toronto-bids nightly* · {_elapsed(elapsed_s)}"]

    if steps:
        lines += ["", "*Steps*"]
        for s in steps:
            icon = _STEP_ICON.get(s.get("status"), "•")
            detail = s.get("detail") or s.get("error") or ""
            dur = s.get("seconds") or 0.0
            suffix = f" · {_elapsed(dur)}" if dur >= 1 else ""
            lines.append(f"{icon} {s.get('name', '?')}  {detail}{suffix}".rstrip())

    if sources:
        # Fetched rows per data source — did each City feed respond, and with how much. NOT
        # rows_upserted: that is written-rows (a COALESCE upsert, one record → many rows), which
        # exceeds fetched and is not "new". The Growth section owns the real new-record counts.
        lines += ["", "*Sources* (fetched)"]
        ok_segs, warns = [], []
        for r in sources:
            fetched = r.get("rows_fetched", 0) or 0
            if r.get("status") != "ok" or fetched == 0:
                extra = "" if r.get("status") == "ok" else f" ({r.get('status')})"
                warns.append(f"⚠ {r.get('source', '?')} {fetched:,} fetched{extra}")
            else:
                ok_segs.append(f"{r.get('source', '?')} {fetched:,}")
        if ok_segs:
            lines.append(" · ".join(ok_segs))
        lines += warns

    growth = _growth(before, after)
    if growth:
        lines += ["", "*Growth*", " · ".join(growth)]

    lines += ["", f"export {'FAILED' if export_bytes is None else f'{export_bytes / 1_048_576:.1f} MiB'}"]

    if failures:
        lines += ["", f"*Failures ({len(failures)})*"]
        lines += [f"{name}: {error}" for name, error in failures]

    return "\n".join(lines)


def post(text: str, webhook: str | None = None, log=lambda _m: None) -> bool:
    """Post to Slack. Returns True if it went out.

    No webhook -> a silent no-op, so a dev machine and CI need no separate code path.
    A failed post is logged and swallowed: the archive outranks the notification, and a dead
    webhook must never turn a good sync into a failed unit.
    """
    webhook = webhook or os.environ.get("TB_SLACK_WEBHOOK")
    if not webhook:
        return False
    try:
        response = httpx.post(webhook, json={"text": text}, timeout=_SLACK_TIMEOUT)
        # Status read by hand, not response.raise_for_status(): that exception's message
        # embeds the request URL — the webhook IS the credential, and this repo is public.
        # log() goes to journald, so raise_for_status() here would write the credential to
        # the system log. Log the status code alone.
        if response.status_code >= 400:
            log(f"  slack post rejected: HTTP {response.status_code}")
            return False
        return True
    except Exception as exc:
        # Log the exception TYPE, not str(exc): httpx.InvalidURL and UnsupportedProtocol embed
        # the request URL in their message, same leak the status-code branch above avoids.
        log(f"  slack post failed: {type(exc).__name__}")
        return False
