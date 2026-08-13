#!/usr/bin/env bash
# Remove obsolete local aliases and aged, unreferenced content-addressed lanes.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

cache="${P101_REPOSITORY_BUILD_CACHE:-}"
max_age_days="${P101_BUILD_CACHE_MAX_AGE_DAYS:-30}"
dry_run=false

usage() {
  cat <<'EOF'
Usage: ./workspace/gc-build-cache.sh --cache <absolute-dir> [--max-age-days N] [--dry-run]

Remove repository-local content-lane symlinks not selected by a current build
marker. Remove their external cache directories only after the requested age;
zero removes every unreferenced cache directory. Marker-selected targets are
never removed.
EOF
}

while (($# > 0)); do
  case "$1" in
    --cache) cache="${2:?Error: --cache requires a directory}"; shift 2 ;;
    --max-age-days) max_age_days="${2:?Error: --max-age-days requires a value}"; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Error: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$cache" in
  /*) ;;
  *) printf 'Error: --cache must be an absolute path\n' >&2; exit 2 ;;
esac
case "$max_age_days" in
  ''|*[!0-9]*) printf 'Error: --max-age-days must be unsigned\n' >&2; exit 2 ;;
esac
[[ -d "$cache" ]] || {
  printf 'Build cache is absent; nothing to collect: %s\n' "$cache"
  exit 0
}
cache="$(CDPATH='' cd -P -- "$cache" && pwd -P)"
[[ -f repos.txt ]] || { printf 'Error: repos.txt is missing\n' >&2; exit 2; }

state="$(mktemp -d "${TMPDIR:-/tmp}/p101-build-cache-gc.XXXXXX")"
trap 'rm -rf -- "$state"' EXIT
protected_targets="$state/protected-targets.txt"
: > "$protected_targets"
aliases_removed=0
lanes_removed=0
bytes_removed=0

trim() {
  local value="${1-}"
  value="${value#"${value%%[![:space:]]*}"}"
  printf '%s' "${value%"${value##*[![:space:]]}"}"
}

content_alias() {
  local name="$1"
  local suffix="${name##*__}"

  [[ "$name" == build-*__* && "${#suffix}" -eq 40 ]] || return 1
  case "$suffix" in *[!0-9a-f]*) return 1 ;; esac
}

remove_path() {
  local path="$1"

  if $dry_run; then
    printf '[dry-run] remove %s\n' "$path"
  else
    rm -rf -- "$path"
  fi
}

while IFS= read -r raw || [[ -n "${raw:-}" ]]; do
  raw="${raw%$'\r'}"
  raw="${raw%%#*}"
  line="$(trim "$raw")"
  [[ -n "$line" ]] || continue
  IFS='|' read -r _url repository _kind <<< "$line"
  repository="$(trim "${repository:-}")"
  [[ -d "$repository" ]] || continue

  selected_quality=""
  selected_runtime=""
  if [[ -f "$repository/.last-build-dir" ]]; then
    IFS= read -r selected_quality < "$repository/.last-build-dir" || true
  fi
  if [[ -f "$repository/.last-runtime-build-dir" ]]; then
    IFS= read -r selected_runtime < "$repository/.last-runtime-build-dir" || true
  fi
  for selected in "$selected_quality" "$selected_runtime"; do
    [[ -n "$selected" && -e "$repository/$selected" ]] || continue
    target="$(CDPATH='' cd -P -- "$repository/$selected" 2>/dev/null && pwd -P || true)"
    case "$target" in "$cache"/*) printf '%s\n' "$target" >> "$protected_targets" ;; esac
  done

  # Exact compiler-lane aliases are the public dependency boundary used by
  # P101Linking. They deliberately coexist for every compiler pair, whereas
  # the marker above names only the host pair selected for installation. Keep
  # every valid stable alias target alive; otherwise an aged cache hit for a
  # non-host compiler can be collected immediately before its acceptance run.
  while IFS= read -r stable_alias || [[ -n "$stable_alias" ]]; do
    [[ -L "$stable_alias" ]] || continue
    name="$(basename -- "$stable_alias")"
    if content_alias "$name"; then
      continue
    fi
    target="$(CDPATH='' cd -P -- "$stable_alias" 2>/dev/null && pwd -P || true)"
    case "$target" in
      "$cache"/*) printf '%s\n' "$target" >> "$protected_targets" ;;
    esac
  done < <(find "$repository" -maxdepth 1 -type l -name 'build-*' -print)

  while IFS= read -r alias || [[ -n "$alias" ]]; do
    [[ -L "$alias" ]] || continue
    name="$(basename -- "$alias")"
    content_alias "$name" || continue
    if [[ "$name" == "$selected_quality" || "$name" == "$selected_runtime" ]]; then
      continue
    fi
    remove_path "$alias"
    aliases_removed=$((aliases_removed + 1))
  done < <(find "$repository" -maxdepth 1 -type l -name 'build-*__*' -print)
done < repos.txt

sort -u -o "$protected_targets" "$protected_targets"
while IFS= read -r lane || [[ -n "$lane" ]]; do
  [[ -d "$lane" ]] || continue
  content_alias "$(basename -- "$lane")" || continue
  if grep -Fqx "$lane" "$protected_targets"; then
    continue
  fi
  if [[ "$max_age_days" -ne 0 ]] &&
     [[ -z "$(find "$lane" -prune -mtime +"$max_age_days" -print -quit)" ]]; then
    continue
  fi
  lane_bytes="$(du -sk "$lane" 2>/dev/null | awk '{print $1 * 1024}')"
  case "$lane_bytes" in ''|*[!0-9]*) lane_bytes=0 ;; esac
  bytes_removed=$((bytes_removed + lane_bytes))
  remove_path "$lane"
  lanes_removed=$((lanes_removed + 1))
done < <(find "$cache" -mindepth 2 -maxdepth 2 -type d -name 'build-*__*' -print)

printf 'Build-cache GC: %d alias(es), %d aged lane(s), %d byte(s)%s.\n' \
  "$aliases_removed" "$lanes_removed" "$bytes_removed" \
  "$($dry_run && printf ' (dry run)' || true)"
