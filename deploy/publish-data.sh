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

fail() { echo "publish-data: $*" >&2; exit 1; }

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

# 3. A token is required — unlike the optional Slack webhook, publishing is the deliverable.
if [ "$DRY_RUN" != 1 ] && [ -z "${GH_TOKEN:-}" ]; then
  fail "GH_TOKEN is unset — cannot publish (add it to ~/.config/toronto-bids/tb.env)"
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

echo "publish-data: done"
