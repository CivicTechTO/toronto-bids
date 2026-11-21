#!/usr/bin/env bash
# Ensure a Node >= TB_NODE_MIN_MAJOR binary is on PATH (probing version, since /usr/bin/node may be too old).
# Uses only bash builtins so repairing PATH does not depend on PATH.

: "${TB_NODE_MIN_MAJOR:=22}"

# Convert "v22.9.0" to a comparable integer (22009000); returns 1 if unparseable.
_tb_ver_key() {
  local v="${1#v}" maj min pat rest
  IFS=. read -r maj min pat rest <<<"$v"
  case "${maj:-}" in ''|*[!0-9]*) return 1 ;; esac
  case "${min:-}" in ''|*[!0-9]*) min=0 ;; esac
  case "${pat:-}" in ''|*[!0-9]*) pat=0 ;; esac
  printf '%d' "$(( maj * 1000000 + min * 1000 + pat ))"
}

_tb_node_key() {
  local ver
  ver="$("$1" --version 2>/dev/null)" || return 1
  _tb_ver_key "$ver"
}

_tb_path_node_ok() {
  local n key
  n="$(command -v node 2>/dev/null)" || return 1
  [ -n "$n" ] || return 1
  key="$(_tb_node_key "$n")" || return 1
  [ "$(( key / 1000000 ))" -ge "$TB_NODE_MIN_MAJOR" ]
}

# Prepend the newest nvm node meeting TB_NODE_MIN_MAJOR if PATH node is missing or too old.
tb_resolve_node() {
  _tb_path_node_ok && return 0

  local cand key best_key="" best_bin=""
  for cand in "${HOME:-}"/.nvm/versions/node/*/bin/node; do
    [ -x "$cand" ] || continue
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
