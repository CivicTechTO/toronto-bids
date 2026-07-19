#!/usr/bin/env bash
# systemd ExecStart wrapper (#146): run the nightly, then publish the export.
#
# Design: docs/superpowers/specs/2026-07-19-publish-data-design.md
#
# publish-data.sh runs EVEN AFTER a partial/failed `tb nightly` — the export is still valid
# whenever any rows exist — and the unit fails if EITHER step failed, so neither masks the other.
#
# Deliberately NOT `set -e`: publish must run regardless of the nightly's exit code, so we capture
# each exit code and combine them ourselves rather than let the first non-zero abort the script.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
UV="${TB_NIGHTLY_UV:-$HOME/.local/bin/uv}"

cd "$HERE/../scrapers" || exit 1

"$UV" run tb nightly
nightly_rc=$?

"$HERE/publish-data.sh"
publish_rc=$?

if [ "$nightly_rc" -ne 0 ] || [ "$publish_rc" -ne 0 ]; then
  echo "tb-nightly-run: nightly_rc=$nightly_rc publish_rc=$publish_rc — unit fails" >&2
  exit 1
fi
echo "tb-nightly-run: nightly and publish both ok"
