#!/usr/bin/env bash
# Shared publish helpers, split out so pytest can source them directly.
# Expects DRY_RUN, UV, and SCRAPERS to be set by the caller.

# Best-effort sync_run logging via `tb record-step` (skipped in dry-run).
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

# Compare remote HEAD Content-Length (following redirects via -L) to the local file size.
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

# Retry clobber uploads on HTTP 5xx only; leave auth and rate-limit failures immediate.
upload_release_assets() {
  local attempt output
  for attempt in 1 2 3; do
    if output="$(gh_run release upload "$@" 2>&1)"; then
      [ -z "$output" ] || printf '%s\n' "$output"
      return 0
    fi
    printf '%s\n' "$output" >&2
    if [ "$attempt" = 3 ] || ! [[ "$output" =~ HTTP[[:space:]]5[0-9][0-9]([^0-9]|$) ]]; then
      return 1
    fi
    echo "publish-data: server error uploading assets; retry $attempt/2" >&2
    sleep "$((attempt * 5))"
  done
}
