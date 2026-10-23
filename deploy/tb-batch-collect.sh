#!/usr/bin/env bash
# Collect ready model results, apply held evidence, export, publish, then acknowledge.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
UV="${TB_NIGHTLY_UV:-$HOME/.local/bin/uv}"
DATA_ROOT="${TB_DATA_DIR:-$HOME/tb-data}"
mkdir -p "$DATA_ROOT" || exit 1
exec 9>"$DATA_ROOT/.pipeline.lock"
flock -n 9 || exit 0 # A long nightly owns the archive; try again next hour.
cd "$HERE/../scrapers" || exit 1
HANDOFF=$(mktemp "$DATA_ROOT/.batch-publish.XXXXXX") || exit 1
trap 'rm -f "$HANDOFF"' EXIT
"$UV" run tb extraction-batches refresh --manifest "$HANDOFF"
collect_rc=$?
publish_rc=0
ack_rc=0
# Supported partial results publish even if another document failed.
if [ -s "$HANDOFF" ]; then
    "$HERE/publish-data.sh"
    publish_rc=$?
    if [ "$publish_rc" -eq 0 ]; then
        "$UV" run tb extraction-batches acknowledge --manifest "$HANDOFF"
        ack_rc=$?
    fi
fi
if [ "$collect_rc" -ne 0 ] || [ "$publish_rc" -ne 0 ] || [ "$ack_rc" -ne 0 ]; then
    echo "tb-batch-collect: collect=$collect_rc publish=$publish_rc acknowledge=$ack_rc" >&2
    exit 1
fi
