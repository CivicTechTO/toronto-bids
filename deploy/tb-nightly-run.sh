#!/usr/bin/env bash
# Run `tb nightly` then publish-data.sh; fail the unit if either fails.
# Avoid `set -e` so publish still runs after a partial/failed nightly.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
UV="${TB_NIGHTLY_UV:-$HOME/.local/bin/uv}"

DATA_ROOT="${TB_DATA_DIR:-$HOME/tb-data}"
mkdir -p "$DATA_ROOT" || exit 1
exec 9>"$DATA_ROOT/.pipeline.lock"
# Wait for a short collector; the collector skips while a long nightly owns it.
flock 9 || exit 1

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
