# Publishing the nightly export (#146)

**Date:** 2026-07-19
**Status:** approved (derived from the frontend spec `2026-07-18-frontend-design.md`, approved
2026-07-18), implemented.
**Builds on:** [`2026-07-17-deployment-design.md`](2026-07-17-deployment-design.md), whose §1
deliberately left the destination TBD ("the artifact lands on the server's disk and stops
there"). This is that destination.

## 1. What this is

The nightly (`tb nightly`) writes `bids.json` to `~/tb-data/export/` on plexbox and stops. The
frontend (`CivicTechTO/toronto-bids-frontend`) is a static site built from that artifact and
needs it at a **stable public URL, refreshed nightly**. This adds the publish step that closes
that gap — without touching anything behind the CLI, exactly as the deployment design wrapped
the CLI without changing it.

Publishing stays **outside** the `tb` CLI. It is a server-and-GitHub concern (a `gh` upload to a
release), not archive logic, and the local-first goal still holds: `tb nightly` produces a valid
local artifact with no cloud dependency, and the publish is a separate seam bolted on by the
systemd unit. The dev laptop and CI never invoke it.

## 2. Design

### 2.1 Rolling `latest` release + monthly snapshots

`deploy/publish-data.sh` uploads three assets to a **rolling `latest`** release on a new repo
`CivicTechTO/toronto-bids-data`:

- `bids.json` — the artifact
- `bids.json.gz` — gzip for the frontend's fetch (bandwidth)
- `bids.sqlite` — the queryable store (the frontend's Datasette-Lite link)

```
gh release upload latest bids.json bids.json.gz bids.sqlite --clobber -R CivicTechTO/toronto-bids-data
```

giving the stable URL `https://github.com/CivicTechTO/toronto-bids-data/releases/download/latest/bids.json`.
GitHub serves release assets with `Access-Control-Allow-Origin: *`, which the frontend's
browser-side Datasette-Lite link requires.

On the **1st of each month** it also cuts a dated `snapshot-YYYY-MM-DD` release with the same
assets — a point-in-time copy a researcher can cite, immune to the rolling overwrite.

Then it triggers the site build:
`gh workflow run deploy.yml -R CivicTechTO/toronto-bids-frontend`.

### 2.2 Fatal vs. best-effort, deliberately

- **Missing artifact, invalid JSON, missing DB, or a failed data upload → fatal** (non-zero
  exit). Publishing the data IS the deliverable; a failure must be visible, not swallowed. This
  is the opposite of the Slack webhook, which is a *notification* subordinate to the archive and
  therefore optional.
- **Unauthenticated `gh` → fatal**, for the same reason (unlike `TB_SLACK_WEBHOOK`, which
  no-ops when unset). The check is `gh auth status`, satisfied by either a `GH_TOKEN` in the
  environment (an unattended service token) or a prior `gh auth login` on the box — so a box
  where `gh` is already logged in needs no duplicate credential.
- **The frontend `workflow run` trigger → best-effort (warn, non-fatal).** The data is already
  published — the acceptance `curl` passes — and letting a downstream trigger's failure report
  the whole publish as failed would mask that success. A warning in the journal is the right
  signal for "data is up, site rebuild didn't kick."

### 2.3 Wiring: publish runs even after a partial sync, and neither step masks the other

The unit's `ExecStart` becomes `deploy/tb-nightly-run.sh`, which:

1. runs `tb nightly`, capturing its exit code;
2. runs `publish-data.sh` **regardless** of that code — because the export is valid whenever any
   rows exist, and a good artifact must not be withheld over one bad feed (the nightly's own
   per-step isolation ethos, extended one level out);
3. exits non-zero if **either** step failed.

`&&` would be wrong twice over: `tb nightly` exits non-zero on a *partial* sync (any source
failed), which must still publish; and a publish failure must not hide a nightly failure or vice
versa. So both always run and the wrapper propagates the max.

If the sync fails catastrophically (the DB never opens, no export written), `publish-data.sh`
finds no `bids.json` and fails — correct: there is genuinely nothing to publish, and the unit is
already failing on the nightly.

### 2.4 Authentication; this repo is public

Publishing needs `gh` authenticated with `repo` scope on `toronto-bids-data` (release write) and
`workflow` scope on `toronto-bids-frontend` (dispatch). Either a prior `gh auth login` on the box
or a `GH_TOKEN` satisfies it. A `GH_TOKEN`, if used, is a credential and lives beside the Slack
webhook in `~/.config/toronto-bids/tb.env` (mode `0600`, never in git), pulled in by the unit's
existing `EnvironmentFile=-`.

### 2.5 Offline self-test

`TB_PUBLISH_DRY_RUN=1 deploy/publish-data.sh` prints every `gh` command instead of running it,
after doing the real artifact/JSON checks and gzip — so the operator (and CI-free local dev) can
confirm the logic against a real export without a token or the data repo existing. The snapshot
day and date are overridable (`TB_PUBLISH_DAY`, `TB_PUBLISH_DATE`) so the 1st-of-month path is
testable on any day. Repo names are overridable (`TB_DATA_REPO`, `TB_FRONTEND_REPO`) for a fork.

## 3. Acceptance

- After a nightly run, `curl -sIL .../releases/download/latest/bids.json` returns 200; the fetched
  body carries a fresh `generated_at`.
- A partial-sync night still publishes (the export is valid) and the unit still reports failure.
- A publish failure (no network/token) does not mask a nightly failure, and vice versa — both run,
  the wrapper propagates whichever failed.

## 4. Out of scope / prerequisites the operator does once

- **Creating `CivicTechTO/toronto-bids-data`** (a one-time GitHub action) and installing the `gh`
  CLI on plexbox. Documented in `deploy/README.md`.
- The frontend repo's `deploy.yml` — owned by the frontend project; until it exists the trigger
  warns and the data still publishes.
