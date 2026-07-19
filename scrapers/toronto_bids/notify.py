"""Operational notification for the nightly job (deployment spec §3.4).

The v1 design dropped Slack notifications from scope ("these can wrap the CLI later if
wanted"); this is that wrapper, and it carries no data — only whether the run worked.

The split is deliberate: `summarize` is pure, so the entire message is tested offline against
fixture counts, and `post` is one HTTP call with nothing to get wrong.
"""
import os

import httpx

# Tables worth a line in a one-line summary. The full set is in `tb status`; this is the
# headline: what the archive is FOR (solicitations, awards, bids) plus the dimension the bids
# feed. Everything else is either derived or quiet.
_HEADLINE = (("solicitation", "solicitations"), ("award", "awards"),
             ("bid", "bids"), ("supplier", "suppliers"))
_SLACK_TIMEOUT = 15.0


def _count(before: dict, after: dict, key: str, label: str) -> str:
    """'solicitations 7,653 (+12)', or without the delta when nothing moved.

    An empty `before` means the before-count did not run (the caller passes {} on that
    failure) — showing a delta against it would render a fake number against a zero nobody
    measured, so it is omitted entirely rather than computed against 0.
    """
    now = after.get(key, 0)
    if not before:
        return f"{label} {now:,}"
    delta = now - before.get(key, 0)
    return f"{label} {now:,}" + (f" ({delta:+,})" if delta else "")


def _elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def summarize(before: dict, after: dict, failures: list, n_sources: int,
              export_bytes: int | None, elapsed_s: float) -> str:
    """The one-line message. Pure — `export_bytes` is passed in, never stat'ed here.

    Posted on EVERY run, not only on failure, and that is the design: the failure mode a
    failures-only alert cannot catch is the timer never firing at all, where silence and health
    look identical. A nightly line makes silence itself the signal.
    """
    def _seg(label, key):
        a = after.get(key)
        if not a:
            return None
        d = a - before.get(key, 0)
        return f"{label} {a:,} (+{d:,})" if d else None

    parts = [_count(before, after, key, label) for key, label in _HEADLINE]
    parts.extend(s for s in (_seg("agency awards", "agency_award"),
                             _seg("agency bids", "agency_bid"),
                             _seg("ariba files", "ariba_attachment")) if s)
    parts.append(f"export {export_bytes / 1_048_576:.1f} MiB" if export_bytes is not None  # binary MiB, matching ls -lh
                 else "export FAILED")
    parts.append(_elapsed(elapsed_s))
    if not failures:
        return f"✅ toronto-bids — {n_sources}/{n_sources} sources ok · " + " · ".join(parts)
    # NOT "N/{n_sources} sources FAILED": `failures` mixes per-source failures from
    # pipeline.sync with whole-step failures (sync, award_summary, export), and calling a
    # dead disk a failed City feed would send someone to the wrong system at 06:00.
    named = ", ".join(f"{name}: {error}" for name, error in failures)
    return f"❌ toronto-bids — {len(failures)} failed · {named} · " + " · ".join(parts)


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
