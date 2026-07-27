#!/usr/bin/env bash
# Put a wrangler-capable Node on PATH, or say so. Sourced by publish-data.sh (#173).
#
# WHY THIS IS NOT `command -v npx`:
# Ubuntu's `nodejs` package installs /usr/bin/node + /usr/bin/npx, and /usr/bin IS on systemd's
# minimal PATH. So a presence check is *satisfied* on the deploy box — by Node 20. Wrangler >= 4
# requires Node >= 22 and exits 1 in under a second. publish-data.sh gated its nvm fallback on
# `! command -v npx`, so the fallback was dead code there, and with wrangler's stderr going to
# /dev/null the R2 mirror of bids.sqlite failed on EVERY nightly run from 2026-07-19 to
# 2026-07-27 while the unit still exited 0. Probe the VERSION, never mere presence.
#
# Deliberately uses only bash builtins — no ls/sort/grep. This is sourced into a script whose
# PATH is the thing under repair, so depending on PATH to repair PATH is how it breaks twice.
#
# Self-test: scrapers/tests/test_resolve_node.py drives this with stub `node` binaries.

# Wrangler's floor. Overridable so bumping wrangler does not mean editing logic.
: "${TB_NODE_MIN_MAJOR:=22}"

# "v22.9.0" -> 22009000, for numeric comparison. Returns 1 on anything unparseable.
_tb_ver_key() {
  local v="${1#v}" maj min pat rest
  IFS=. read -r maj min pat rest <<<"$v"
  case "${maj:-}" in ''|*[!0-9]*) return 1 ;; esac
  case "${min:-}" in ''|*[!0-9]*) min=0 ;; esac
  case "${pat:-}" in ''|*[!0-9]*) pat=0 ;; esac
  printf '%d' "$(( maj * 1000000 + min * 1000 + pat ))"
}

# Version key of a node binary, or 1 if it is missing / not runnable / not a node.
_tb_node_key() {
  local ver
  ver="$("$1" --version 2>/dev/null)" || return 1
  _tb_ver_key "$ver"
}

# Is the `node` already on PATH new enough for wrangler?
_tb_path_node_ok() {
  local n key
  n="$(command -v node 2>/dev/null)" || return 1
  [ -n "$n" ] || return 1
  key="$(_tb_node_key "$n")" || return 1
  [ "$(( key / 1000000 ))" -ge "$TB_NODE_MIN_MAJOR" ]
}

# Prepend the newest *suitable* nvm node to PATH when the one on PATH is too old or absent.
# Returns 0 with PATH usable, or 1 when no Node >= $TB_NODE_MIN_MAJOR exists anywhere.
#
# "Newest suitable", not "newest": `sort -V | tail -1` blindly takes the highest version even
# when it is below the floor, which would reintroduce exactly this bug on a box whose only nvm
# install is old.
tb_resolve_node() {
  _tb_path_node_ok && return 0

  local cand key best_key="" best_bin=""
  for cand in "${HOME:-}"/.nvm/versions/node/*/bin/node; do
    [ -x "$cand" ] || continue                  # unmatched glob stays literal; -x rejects it
    key="$(_tb_node_key "$cand")" || continue
    [ "$(( key / 1000000 ))" -ge "$TB_NODE_MIN_MAJOR" ] || continue
    if [ -z "$best_key" ] || [ "$key" -gt "$best_key" ]; then
      best_key="$key"
      best_bin="${cand%/node}"
    fi
  done

  [ -n "$best_bin" ] || return 1
  PATH="$best_bin:$PATH"
  export PATH
}
