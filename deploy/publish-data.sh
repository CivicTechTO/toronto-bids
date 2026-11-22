#!/usr/bin/env bash
# Publish the nightly export to GitHub Releases and trigger the frontend build. See deploy/README.md.
#
# Env overrides: TB_PUBLISH_DRY_RUN=1, TB_PUBLISH_DAY, TB_PUBLISH_DATE,
# TB_DATA_REPO, TB_FRONTEND_REPO, TB_R2_PUBLIC_URL.
#
# Avoid `set -e`: guard fallible steps with `|| fail` so publish reaches a definite outcome.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRAPERS="$HERE/../scrapers"
UV="${TB_NIGHTLY_UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}"

DATA_DIR="${TB_DATA_DIR:-$HOME/tb-data}"
EXPORT_DIR="$DATA_DIR/export"
JSON="$EXPORT_DIR/bids.json"
GZ="$EXPORT_DIR/bids.json.gz"
SQLITE="$DATA_DIR/bids.sqlite"
SCHEMA="$EXPORT_DIR/schema.json"
MANIFEST="$EXPORT_DIR/manifest.json"
CSV_ZIP="$EXPORT_DIR/bids-csv.zip"

DATA_REPO="${TB_DATA_REPO:-CivicTechTO/toronto-bids-data}"
FRONTEND_REPO="${TB_FRONTEND_REPO:-CivicTechTO/toronto-bids-frontend}"

DAY="${TB_PUBLISH_DAY:-$(date +%d)}"
SNAPSHOT_DATE="${TB_PUBLISH_DATE:-$(date +%F)}"
DRY_RUN="${TB_PUBLISH_DRY_RUN:-0}"

# Best-effort Slack post; webhook is never logged.
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

. "$HERE/publish-lib.sh"

fail() {
  record_step publish failed "$*"
  slack_notify "❌ toronto-bids publish — $*"
  echo "publish-data: $*" >&2
  exit 1
}

gh_run() {
  if [ "$DRY_RUN" = 1 ]; then
    echo "DRY-RUN gh $*"
  else
    gh "$@"
  fi
}

[ -f "$JSON" ]   || fail "no export at $JSON — nothing to publish"
[ -f "$SQLITE" ] || fail "no database at $SQLITE"

GENERATED_AT="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["meta"]["generated_at"])' "$JSON")" \
  || fail "export is not valid JSON with meta.generated_at: $JSON"
echo "publish-data: export generated_at=$GENERATED_AT"

if [ "$DRY_RUN" != 1 ] && ! gh auth status >/dev/null 2>&1; then
  fail "gh is not authenticated — set GH_TOKEN in ~/.config/toronto-bids/tb.env or run 'gh auth login'"
fi

gzip -9 -c "$JSON" > "$GZ" || fail "gzip failed"

[ -f "$SCHEMA" ] || fail "no schema.json at $SCHEMA — did 'tb export' run?"
[ -f "$CSV_ZIP" ] || fail "no bids-csv.zip at $CSV_ZIP — did 'tb export' run?"
PARQUET_FILES=("$EXPORT_DIR"/*.parquet)
[ -e "${PARQUET_FILES[0]}" ] || fail "no *.parquet in $EXPORT_DIR — did 'tb export' run?"

# Generate manifest from the exact bytes about to be uploaded.
"$UV" run --project "$SCRAPERS" tb manifest \
  "$JSON" "$GZ" "$SQLITE" "$CSV_ZIP" "${PARQUET_FILES[@]}" --out "$MANIFEST" \
  || fail "could not generate manifest.json"

ASSETS=("$JSON" "$GZ" "$SQLITE" "$SCHEMA" "$CSV_ZIP" "${PARQUET_FILES[@]}")

# Ensure rolling `latest` release exists, then upload assets before manifest.
if ! gh_run release view latest -R "$DATA_REPO" >/dev/null 2>&1; then
  gh_run release create latest -R "$DATA_REPO" \
    --title "Latest data" \
    --notes "Rolling nightly export, overwritten every night. See snapshot-* releases for point-in-time copies." \
    || fail "could not create the 'latest' release on $DATA_REPO"
fi
upload_release_assets latest "${ASSETS[@]}" --clobber -R "$DATA_REPO" \
  || fail "upload to the 'latest' release on $DATA_REPO failed"
upload_release_assets latest "$MANIFEST" --clobber -R "$DATA_REPO" \
  || fail "manifest upload to the 'latest' release on $DATA_REPO failed"

# One-time archive of the final Bid Award Panel agenda corpus (best-effort).
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

# Mirror bids.sqlite to CORS-enabled R2 for browser-side Datasette-Lite (best-effort).
R2_BUCKET="${TB_R2_BUCKET:-toronto-bids-data}"
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  echo "publish-data: R2 not configured (no CLOUDFLARE_API_TOKEN) — skipping the bucket mirror"
elif [ "$DRY_RUN" = 1 ]; then
  echo "DRY-RUN python -m toronto_bids.r2_mirror --bucket $R2_BUCKET --file $SQLITE"
else
  if _r2_err="$(cd "$SCRAPERS" && "$UV" run --locked python -m toronto_bids.r2_mirror \
        --bucket "$R2_BUCKET" --file "$SQLITE" 2>&1)"; then
    echo "publish-data: mirrored bids.sqlite to R2 bucket $R2_BUCKET"
    record_step r2_mirror ok
  else
    echo "publish-data: WARNING — R2 upload of bids.sqlite failed (release is published)" >&2
    printf 'publish-data: R2 uploader said: %s\n' "$_r2_err" >&2
    record_step r2_mirror failed "$_r2_err"
  fi
fi

# Verify live public artifact sizes against local bids.sqlite (read-only, best-effort).
if [ "$DRY_RUN" = 1 ]; then
  echo "DRY-RUN skip artifact freshness verification"
else
  verify_artifact_size github_release_freshness \
    "https://github.com/${DATA_REPO}/releases/download/latest/bids.sqlite" "$SQLITE"
  if [ -n "${CLOUDFLARE_API_TOKEN:-}" ]; then
    verify_artifact_size r2_freshness \
      "${TB_R2_PUBLIC_URL:-https://pub-99a890c186c743c19ef7bcd00024dca8.r2.dev}/bids.sqlite" \
      "$SQLITE"
  fi
fi

# Cut a dated monthly snapshot on the 1st (idempotent).
if [ "$DAY" = 01 ]; then
  TAG="snapshot-$SNAPSHOT_DATE"
  if gh_run release view "$TAG" -R "$DATA_REPO" >/dev/null 2>&1; then
    echo "publish-data: snapshot $TAG already exists — skipping"
  else
    gh_run release create "$TAG" -R "$DATA_REPO" \
      --title "Snapshot $SNAPSHOT_DATE" \
      --notes "Point-in-time export, generated_at=$GENERATED_AT." \
      "${ASSETS[@]}" "$MANIFEST" \
      || fail "could not create snapshot release $TAG on $DATA_REPO"
  fi
fi

# Trigger frontend deploy (best-effort; recorded separately from data publish).
frontend_warning=""
if frontend_output="$(gh_run workflow run deploy.yml -R "$FRONTEND_REPO" 2>&1)"; then
  [ -z "$frontend_output" ] || printf '%s\n' "$frontend_output"
  record_step frontend_deploy_trigger ok
else
  frontend_error="could not trigger the frontend deploy on $FRONTEND_REPO: ${frontend_output:-gh workflow run failed}"
  echo "publish-data: WARNING — $frontend_error (data is published)" >&2
  record_step frontend_deploy_trigger failed "$frontend_error"
  frontend_warning=" · ⚠ frontend deploy trigger failed — ${frontend_output:-gh workflow run failed}"
fi

record_step publish ok
slack_notify "✅ toronto-bids publish — latest release updated · ${GENERATED_AT} · https://github.com/${DATA_REPO}/releases/tag/latest${frontend_warning}"

echo "publish-data: done"
