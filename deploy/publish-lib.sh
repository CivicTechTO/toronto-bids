#!/usr/bin/env bash
# Shared helpers for publish-data.sh, split out for testability (#176) — the same reasoning
# that split resolve-node.sh out in #173: a small sourceable file lets pytest drive these
# functions directly instead of the whole publish flow (a real GitHub release, wrangler, Slack).
#
# Expects DRY_RUN, UV, SCRAPERS to already be set by the sourcing script.

# Write one sync_run row via `tb record-step` (#176). publish-data.sh is bash, downstream of
# `tb nightly` in its own systemd unit, so it can't call `_run_step` directly — before this it
# reported its own outcome only to the journal and Slack, and `tb status` read 14/14 ok while
# this very script's R2 mirror had been dead for 8 nights (#173). Best-effort and silent on its
# own failure: a monitoring convenience call must never be why the actual publish looks broken.
# Skipped in dry-run — nothing real happened, so nothing real should be recorded.
record_step() {
  local name="$1" status="$2" err="${3:-}"
  if [ "$DRY_RUN" = 1 ]; then
    echo "DRY-RUN record-step $name $status${err:+ ($err)}"
    return 0
  fi
  if [ -n "$err" ]; then
    "$UV" run --project "$SCRAPERS" tb record-step "$name" "$status" --error "$err" >/dev/null 2>&1
  else
    "$UV" run --project "$SCRAPERS" tb record-step "$name" "$status" >/dev/null 2>&1
  fi || echo "publish-data: WARNING — could not record step '$name' to sync_run" >&2
}

# Verify an artifact against ITSELF — a HEAD request's Content-Length compared to the local
# file's own size — rather than against the exit code of the command that was supposed to
# write it (#176, deferred from the comment on #173). This is a stronger check than "did
# wrangler/gh exit 0": it runs regardless of which upload branch fired tonight, so it also
# catches a silent short-write that leaves an upload command reporting success on a truncated
# object. Read-only; never retries or re-uploads, only reports. `curl -sIL`, not `-I`, because
# a GitHub release asset 302s to a signed Azure blob URL — `-L` follows it and `tail -1` on the
# grepped Content-Length lines takes the FINAL response's, not the redirect's (which is 0).
verify_artifact_size() {
  local label="$1" url="$2" local_file="$3"
  local local_size remote_size
  local_size="$(wc -c < "$local_file" | tr -d ' ')"
  remote_size="$(curl -sIL --max-time 20 "$url" 2>/dev/null \
    | tr -d '\r' | grep -i '^content-length:' | tail -1 | cut -d' ' -f2)"
  if [ -z "$remote_size" ]; then
    echo "publish-data: WARNING — could not verify $label (no Content-Length from $url)" >&2
    record_step "$label" failed "no Content-Length from $url"
    return 1
  fi
  if [ "$remote_size" != "$local_size" ]; then
    echo "publish-data: WARNING — $label size mismatch: local=$local_size remote=$remote_size ($url)" >&2
    record_step "$label" failed "size mismatch: local=$local_size remote=$remote_size"
    return 1
  fi
  echo "publish-data: verified $label ($remote_size bytes live at $url)"
  record_step "$label" ok
  return 0
}
