#!/usr/bin/env bash
# Publish the nightly export to a public GitHub release, then trigger the frontend build. (#146)
#
# Design: docs/superpowers/specs/2026-07-19-publish-data-design.md
#
# Runs on the server AFTER `tb nightly`, wrapped by tb-nightly-run.sh so it runs even after a
# partial sync — the export is valid whenever any rows exist, and a good artifact must not be
# withheld over one bad feed (the nightly's own per-step isolation ethos, extended one level out).
#
# Uploads bids.json, bids.json.gz, bids.sqlite to a rolling `latest` release, giving the static
# frontend a stable URL. On the 1st of the month it also cuts a dated snapshot-YYYY-MM-DD release
# (point-in-time citation). Then it triggers the frontend deploy (best-effort).
#
# GH_TOKEN (a credential) lives in ~/.config/toronto-bids/tb.env, mode 0600, never in git — the
# service pulls it in via EnvironmentFile.
#
# Self-test: TB_PUBLISH_DRY_RUN=1 prints every `gh` command instead of running it, after the real
# artifact/JSON checks and gzip. TB_PUBLISH_DAY / TB_PUBLISH_DATE force the snapshot path on any
# day; TB_DATA_REPO / TB_FRONTEND_REPO override the targets for a fork.
#
# Deliberately NOT `set -e`: every fallible step is guarded with `|| fail`, so publish runs to a
# definite success/failure rather than dying mid-way and leaving a half-updated release.
set -uo pipefail

DATA_DIR="${TB_DATA_DIR:-$HOME/tb-data}"
EXPORT_DIR="$DATA_DIR/export"
JSON="$EXPORT_DIR/bids.json"
GZ="$EXPORT_DIR/bids.json.gz"
SQLITE="$DATA_DIR/bids.sqlite"

DATA_REPO="${TB_DATA_REPO:-CivicTechTO/toronto-bids-data}"
FRONTEND_REPO="${TB_FRONTEND_REPO:-CivicTechTO/toronto-bids-frontend}"

DAY="${TB_PUBLISH_DAY:-$(date +%d)}"
SNAPSHOT_DATE="${TB_PUBLISH_DATE:-$(date +%F)}"
DRY_RUN="${TB_PUBLISH_DRY_RUN:-0}"

# Post one line to Slack (best-effort). The webhook is a credential and this repo is public, so
# it is passed ONLY as the curl URL — never echoed or logged. No webhook -> no-op; dry-run echoes.
slack_notify() {
  local msg="$1"
  if [ "$DRY_RUN" = 1 ]; then
    echo "DRY-RUN slack: $msg"
    return 0
  fi
  [ -n "${TB_SLACK_WEBHOOK:-}" ] || return 0
  curl -s -o /dev/null --max-time 15 \
    -H 'Content-Type: application/json' \
    --data "$(printf '{"text": %s}' "$(printf '%s' "$msg" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")" \
    "$TB_SLACK_WEBHOOK" || echo "publish-data: WARNING — slack post failed" >&2
}

fail() { slack_notify "❌ toronto-bids publish — $*"; echo "publish-data: $*" >&2; exit 1; }

gh_run() {
  if [ "$DRY_RUN" = 1 ]; then
    echo "DRY-RUN gh $*"
  else
    gh "$@"
  fi
}

# 1. The artifact must exist. On a catastrophic nightly (DB never opened, nothing exported)
#    there is no file — that is a publish failure, surfaced, not a silent skip.
[ -f "$JSON" ]   || fail "no export at $JSON — nothing to publish"
[ -f "$SQLITE" ] || fail "no database at $SQLITE"

# 2. The export must be valid JSON carrying generated_at. A truncated write is worse than no
#    update — better to fail loudly and keep last night's good release than clobber it with junk.
GENERATED_AT="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["meta"]["generated_at"])' "$JSON")" \
  || fail "export is not valid JSON with meta.generated_at: $JSON"
echo "publish-data: export generated_at=$GENERATED_AT"

# 3. gh must be authenticated — unlike the optional Slack webhook, publishing is the deliverable,
#    so unauthenticated is a hard failure. Either a GH_TOKEN in the environment (an unattended
#    service token) or a prior `gh auth login` on the box satisfies this; `gh auth status` covers
#    both, so we don't force a duplicate credential onto a box where gh is already logged in.
if [ "$DRY_RUN" != 1 ] && ! gh auth status >/dev/null 2>&1; then
  fail "gh is not authenticated — set GH_TOKEN in ~/.config/toronto-bids/tb.env or run 'gh auth login'"
fi

# 4. Fresh gzip beside the json.
gzip -9 -c "$JSON" > "$GZ" || fail "gzip failed"

ASSETS=("$JSON" "$GZ" "$SQLITE")

# 5. Ensure the rolling `latest` release exists, then clobber its assets.
if ! gh_run release view latest -R "$DATA_REPO" >/dev/null 2>&1; then
  gh_run release create latest -R "$DATA_REPO" \
    --title "Latest data" \
    --notes "Rolling nightly export, overwritten every night. See snapshot-* releases for point-in-time copies." \
    || fail "could not create the 'latest' release on $DATA_REPO"
fi
gh_run release upload latest "${ASSETS[@]}" --clobber -R "$DATA_REPO" \
  || fail "upload to the 'latest' release on $DATA_REPO failed"

# 5b. Preserve the FINAL Bid Award Panel agenda corpus as an immutable data asset (#unified-
#     nightly). The Panel is abolished; these 891 pages never change, so upload once and skip
#     forever after — a cheap `release view` check per night, no re-upload. Best-effort: the
#     data publish above is the deliverable; a missing agenda archive must not fail the run.
AGENDAS_DIR="${TB_AGENDAS_DIR:-$DATA_DIR/council/agendas}"
if gh_run release view council-agendas -R "$DATA_REPO" >/dev/null 2>&1; then
  echo "publish-data: council-agendas archive already present — skipping"
elif [ -d "$AGENDAS_DIR" ] && [ -n "$(ls -A "$AGENDAS_DIR" 2>/dev/null)" ]; then
  AGENDAS_ZIP="$EXPORT_DIR/council-agendas.zip"
  ( cd "$AGENDAS_DIR" && zip -q -r -X "$AGENDAS_ZIP" . ) \
    && gh_run release create council-agendas -R "$DATA_REPO" \
         --title "Bid Award Panel agenda corpus (final)" \
         --notes "The 891 cached Bid Award Panel / Bid Committee agendas — the complete, final corpus (the Panel was abolished 2025-10-01). Unpack into <DATA_DIR>/council/agendas/ and run 'tb enrich-titles' to re-derive titles/bids/composite. Immutable." \
         "$AGENDAS_ZIP" \
    || echo "publish-data: WARNING — could not publish the council-agendas archive (data is published)" >&2
else
  echo "publish-data: WARNING — no cached agendas at $AGENDAS_DIR to archive" >&2
fi

# 5c. Mirror bids.sqlite to the CORS-enabled R2 bucket for browser-side Datasette-Lite (#155).
#     GitHub release assets no longer send CORS, so the in-browser SQL page loads the DB from R2
#     instead. Reuses CLOUDFLARE_API_TOKEN (already in tb.env for provisioning) via wrangler —
#     overwrite the same object each night so the URL is stable. Best-effort: the GitHub release
#     is the deliverable; a failed R2 push leaves last night's copy and only warns. Skips cleanly
#     when R2 is not configured (dev/CI, or before the account is set up).
R2_BUCKET="${TB_R2_BUCKET:-toronto-bids-data}"
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  echo "publish-data: R2 not configured (no CLOUDFLARE_API_TOKEN) — skipping the bucket mirror"
elif [ "$DRY_RUN" = 1 ]; then
  echo "DRY-RUN wrangler r2 object put $R2_BUCKET/bids.sqlite --file $SQLITE --remote"
else
  # systemd's PATH is minimal; make node/npx findable, preferring the newest nvm node.
  if ! command -v npx >/dev/null 2>&1; then
    _node_bin="$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1)"
    [ -n "$_node_bin" ] && PATH="$_node_bin:$PATH"
  fi
  # Pinned version so a warm npx cache is reused (no nightly re-resolve of "latest").
  if npx -y wrangler@4.112.0 r2 object put "$R2_BUCKET/bids.sqlite" \
        --file "$SQLITE" --remote --content-type application/x-sqlite3 >/dev/null 2>&1; then
    echo "publish-data: mirrored bids.sqlite to R2 bucket $R2_BUCKET"
  else
    echo "publish-data: WARNING — R2 upload of bids.sqlite failed (release is published)" >&2
  fi
fi

# 6. On the 1st, cut a dated snapshot for citation (idempotent — skip if it already exists).
if [ "$DAY" = 01 ]; then
  TAG="snapshot-$SNAPSHOT_DATE"
  if gh_run release view "$TAG" -R "$DATA_REPO" >/dev/null 2>&1; then
    echo "publish-data: snapshot $TAG already exists — skipping"
  else
    gh_run release create "$TAG" -R "$DATA_REPO" \
      --title "Snapshot $SNAPSHOT_DATE" \
      --notes "Point-in-time export, generated_at=$GENERATED_AT." \
      "${ASSETS[@]}" \
      || fail "could not create snapshot release $TAG on $DATA_REPO"
  fi
fi

# 7. Trigger the frontend build against the new data — BEST-EFFORT. The data is already
#    published (the acceptance curl passes); a failed downstream trigger must not report the
#    whole publish as failed and mask that success. A warning is the right signal.
if ! gh_run workflow run deploy.yml -R "$FRONTEND_REPO"; then
  echo "publish-data: WARNING — could not trigger the frontend deploy on $FRONTEND_REPO (data is published)" >&2
fi

slack_notify "✅ toronto-bids publish — latest release updated · ${GENERATED_AT} · https://github.com/${DATA_REPO}/releases/tag/latest"

echo "publish-data: done"
