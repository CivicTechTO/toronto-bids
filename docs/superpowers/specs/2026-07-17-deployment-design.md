# Deployment: the nightly job on plexbox

**Date:** 2026-07-17
**Status:** approved, not yet implemented
**Supersedes:** the v1 non-goal "Slack notifications, a Docker image, and a scheduler are
explicitly dropped from v1 (the operational surface is a blank slate; these can wrap the CLI
later if wanted)" — [2026-07-14 rewrite design](2026-07-14-toronto-bids-scraper-rewrite-design.md)
§1. This is that later. It wraps the CLI and changes nothing behind it.

## 1. What this is

The archive has run by hand on a laptop since the rewrite. This puts it on a home server on a
timer, so the record keeps accruing without anyone remembering to run it, and so a failure is
announced rather than discovered.

**It does not publish anything.** The rewrite design's goal 2 — "local-first: produce a clean,
queryable local dataset with **no cloud dependency**. Publishing to any destination is a
separate, optional concern behind an isolated seam (destination is TBD)" — still holds. The
destination is still TBD. The artifact lands on the server's disk and stops there. Slack gets
an operational summary, not data.

## 2. The target, measured

`plexbox` @ `ssh.plexstone.stream`, probed 2026-07-17:

| | |
|---|---|
| OS | Ubuntu 24.04.4 LTS, x86_64, 8 cores, 15 GB RAM |
| systemd | 255 — user units running, `Linger=yes` already set for `alex` |
| Disk | 914 G, 497 G free |
| Present | `git` 2.43, `rsync` 3.2.7, `python3` 3.12.3 (project needs ≥3.12) |
| Missing | `uv`, `pdftotext`, `sqlite3` |
| Clock | synced, `Etc/UTC` |
| Reachability | OData spine 200, CKAN 200, Ariba 405 (that endpoint is POST-only; 405 proves reach) |

**No passwordless sudo.** This drives the user-vs-system decision below.

**A full sync takes ~3 minutes**, not "slow" as CLAUDE.md claims — measured from `sync_run`
(14:13:30 → 14:16:35 on 2026-07-16), and ~2m45s of it is `ariba_discovery`. The timer design
does not need to accommodate a long job.

## 3. Decisions

### 3.1 User-level systemd, not system-level

Units in `~/.config/systemd/user/`, code in `~/toronto-bids`, data in `~/tb-data`.

The server has no passwordless sudo, so a system unit would need a password for every future
change. Linger is already enabled, so user units survive logout — which is the only thing
system units would have bought. **Exactly one privileged command is needed, once:**

```shell
sudo apt install -y poppler-utils pipx
```

`uv` then installs unprivileged via `pipx install uv`. Deliberately not `curl … | sh`: distro
packages over piping a remote script into a shell.

### 3.2 `TB_DATA_DIR` points outside the checkout

`TB_DATA_DIR=~/tb-data`. The default is `scrapers/files/`, i.e. inside the repo — fine for
development, wrong for a server, where a `git pull` should never be able to touch 26 GB of
archive. `config.py` already reads the env var; nothing to build.

### 3.3 A new `tb nightly` subcommand

```
tb nightly  =  sync  →  enrich-awards --download  →  export  →  post summary
```

The alternative is three `ExecStart=` lines and no summary. The summary needs before/after row
counts and per-source status — `db.counts()` and the `sync_run` table have both — and
recovering them by parsing the CLI's stdout would be a whitespace parser over chatty output,
which is the exact failure mode #116 just finished removing from this codebase.

Semantics, mirroring `pipeline.run_source`'s existing isolation:

- Each step is isolated: a failing step never stops the ones behind it.
- **Export runs even after a partial sync.** Rows are committed per-source and never deleted;
  partial data is still data. Skipping the export would discard a good artifact over one bad
  feed.
- Exit non-zero if any step failed, so systemd marks the unit failed.

### 3.4 Slack: compact summary every run, loud on failure

New `toronto_bids/notify.py`:

- `summarize(before, after, runs) -> str` — **pure**, unit-tested offline with no webhook and
  no network.
- `post(webhook, text)` — one httpx call.

```
✅ toronto-bids — 9 sources ok · solicitations 7,653 (+12) · awards 14,165 (+8)
   · bids 18,632 (+5) · export 29.4 MB · 3m12s
❌ toronto-bids — FAILED 1/9: ariba_discovery — HTTPError 500 · other sources ok, export written
```

Two rules:

- **`TB_SLACK_WEBHOOK` unset → `post` is a no-op.** This Mac and CI stay silent without a
  separate code path, the same way the rewrite design's goal 3 has auth-optional components
  "degrade gracefully when credentials are absent".
- **A Slack failure never fails the run.** The archive outranks the notification; the post is
  wrapped and logged.

Every-run posting is deliberate. It doubles as a heartbeat: the failure mode a failures-only
alert cannot catch is the timer never firing, where silence and health look identical.

### 3.5 The webhook is a credential and this repo is public

`~/.config/toronto-bids/tb.env`, mode `0600`, referenced by `EnvironmentFile=`. Never in git,
never in a unit file, never pasted into a transcript. The value is written on the server by
the operator:

```shell
ssh ssh.plexstone.stream 'read -rs W && printf "TB_SLACK_WEBHOOK=%s\n" "$W" > ~/.config/toronto-bids/tb.env && chmod 600 ~/.config/toronto-bids/tb.env'
```

### 3.6 Timer

```ini
OnCalendar=*-*-* 05:30:00 America/Toronto
RandomizedDelaySec=30m
Persistent=true
```

Verified on the box: `systemd-analyze calendar` normalizes this and resolves to 09:30 UTC, so
systemd owns the DST arithmetic rather than us. The box is on UTC, so a naive `OnCalendar=daily`
would fire at 20:00 Toronto. `Persistent=true` runs a missed job on boot — a home server is
not assumed to be up. `RandomizedDelaySec` keeps us off the City's endpoints at a round number.

`TimeoutStartSec=30m` against a measured 3-minute run.

## 4. Migration

`rsync` from the laptop. The server becomes primary; the laptop becomes a dev box.

**`files/documents/award_summary/` is not optional.** Since #116, `store_award_summary_bids`
reads each form's cells off the PDF on disk. With only the database, all 229 forms log
`unreadable` and 1,058 bids silently vanish from the next export.

What moves (26 GB total): `bids.sqlite` (26 MB), `documents/` (121 MB), `council/` (115 MB, the
891 cached agendas), `legacy/` (26 GB, of which 16 GB is the Azure Ariba rescue).

**The rescue corpus is the one irreplaceable thing here.** It is gitignored, it exists on one
disk, and the City no longer serves it. Moving it to the server does not back it up — it
relocates it. A real second copy is out of scope for this spec and is worth its own issue.

## 5. Error handling

| failure | behaviour |
|---|---|
| one source fails | `sync_run` records it; other sources and the export continue; Slack names it; unit exits non-zero |
| every source fails | same path, all named |
| Slack unreachable | logged, run still succeeds |
| `TB_SLACK_WEBHOOK` unset | `post` no-ops silently |
| poppler missing | every form download is caught, logged `skipped`, and **retried forever** (`download_award_summaries` queues on `sha256 IS NULL`, and the row is never written) — hence poppler is a hard prerequisite, not a nicety |
| server off at 05:30 | `Persistent=true` fires it on next boot |

## 6. Testing

- `summarize()` is pure → unit tests against fixture counts and fake `sync_run` rows, offline,
  in the existing suite.
- `post()` no-ops without the env var → one test asserting no HTTP call.
- `tb nightly`'s step isolation → a test where a step raises and the export still runs.
- The deployment itself is verified by one manual `systemctl --user start tb-nightly.service`
  and reading `journalctl --user -u tb-nightly`, before the timer is enabled.

## 7. Non-goals

- **No publish destination.** Still TBD, still behind the export seam.
- **No Docker.** The rewrite design dropped it; the host is not container-first, and a 26 GB
  bind mount plus a poppler layer buys nothing here.
- **No auto-update.** `git pull && uv sync --locked` stays manual.
- **No dashboard, no metrics, no second notification channel.**
- **No browser on the scheduled path.** `enrich-council` (3 suspended firms, rare) and
  `enrich-titles --scrape` (which *will never find another agenda* — the Bid Award Panel was
  abolished 2025-10-01 and 891 cached pages is the final corpus) stay manual. Playwright, the
  `council` extra, and Xvfb are not installed.

## 8. Known wart

Poppler is installed solely so `download_pdf` can shell out to `pdftotext` and store text that
**nothing reads** — #116 made `background_pdf.text` archival for award summaries, and the bids
now come from the PDF's cells. It is one apt package, so it is not worth changing today, but if
`enrich-awards` ever needs to run where poppler is absent, making that extraction optional is a
two-line change.
