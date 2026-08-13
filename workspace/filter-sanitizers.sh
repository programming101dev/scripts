#!/usr/bin/env bash

# Reduce a requested sanitizer list to groups that the selected compiler can
# compile, link, AND start on this host. Individually supported groups that
# fail when combined remain a hard configuration error.

set -euo pipefail

SCRIPT_ROOT="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=../shared/compilers.sh
. "$SCRIPT_ROOT/shared/compilers.sh"
COMPILER_PLATFORM_ARG="$(p101_compiler_platform_argument)"

usage() {
  printf 'Usage: %s <C compiler> <flags directory> <sanitizer-list>\n' "$0" >&2
  exit "${1:-2}"
}

[[ $# -eq 3 ]] || usage 2

compiler="$1"
flags_dir="$2"
requested="$3"

COMPILER_DEFAULT_CONFIG_ARG="$(p101_compiler_default_config_argument "$compiler")"
COMPILER_PLATFORM_ARGS=()
if [[ -n "$COMPILER_DEFAULT_CONFIG_ARG" ]]; then
  COMPILER_PLATFORM_ARGS+=("$COMPILER_DEFAULT_CONFIG_ARG")
fi
if [[ -n "$COMPILER_PLATFORM_ARG" ]]; then
  COMPILER_PLATFORM_ARGS+=("$COMPILER_PLATFORM_ARG")
fi

[[ -x "$compiler" ]] || {
  printf 'Error: compiler is not executable: %s\n' "$compiler" >&2
  exit 2
}
[[ -d "$flags_dir" ]] || {
  printf 'Error: sanitizer flags directory does not exist: %s\n' "$flags_dir" >&2
  exit 2
}

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-sanitizers.XXXXXX")"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

printf 'int main(void){return 0;}\n' > "$tmp_dir/probe.c"

run_probe_with_timeout() {
  local executable="$1"
  local process_id
  local status
  local watchdog_id

  "$executable" &
  process_id=$!
  (
    sleep 5
    kill -TERM "$process_id" 2>/dev/null || true
  ) &
  watchdog_id=$!
  if wait "$process_id"; then
    status=0
  else
    status=$?
  fi
  kill -TERM "$watchdog_id" 2>/dev/null || true
  wait "$watchdog_id" 2>/dev/null || true
  return "$status"
}

kept_names=()
kept_flags=()
dropped_names=()

old_ifs="$IFS"
IFS=','
read -r -a requested_names <<< "$requested"
IFS="$old_ifs"

for sanitizer in "${requested_names[@]+"${requested_names[@]}"}"; do
  sanitizer="$(printf '%s' "$sanitizer" |
    sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  [[ -n "$sanitizer" ]] || continue

  flag_file="${flags_dir}/${sanitizer}_sanitizer_flags.txt"
  sanitizer_flags=""
  if [[ -f "$flag_file" ]]; then
    sanitizer_flags="$(
      sed 's/#.*//; s/"/ /g' "$flag_file" |
        grep -m1 -- '-fsanitize=' || true
    )"
  fi

  # A syntax-only compile is insufficient: some targets accept a sanitizer
  # option but have no runtime library with which to link an executable.
  # shellcheck disable=SC2086
  if [[ -n "$sanitizer_flags" ]] &&
     "$compiler" $sanitizer_flags "$tmp_dir/probe.c" \
       "${COMPILER_PLATFORM_ARGS[@]}" \
       -o "$tmp_dir/probe" >/dev/null 2>&1 &&
     run_probe_with_timeout "$tmp_dir/probe" >/dev/null 2>&1; then
    kept_names+=("$sanitizer")
    kept_flags+=("$sanitizer_flags")
  else
    dropped_names+=("$sanitizer")
  fi
done

if ((${#kept_flags[@]} > 0)); then
  combined_flags="${kept_flags[*]}"
  # shellcheck disable=SC2086
  if ! combined_error="$(
    "$compiler" $combined_flags "$tmp_dir/probe.c" \
      "${COMPILER_PLATFORM_ARGS[@]}" \
      -o "$tmp_dir/probe" 2>&1 &&
      run_probe_with_timeout "$tmp_dir/probe" 2>&1
  )"; then
    printf 'Error: the selected sanitizers (%s) cannot be combined for %s:\n' \
      "$(IFS=,; printf '%s' "${kept_names[*]}")" "$compiler" >&2
    printf '%s\n' "$combined_error" | head -5 | sed 's/^/  | /' >&2
    printf 'See flag_report/<cc>-sanitize-combos.txt for the full pairwise matrix.\n' >&2
    exit 2
  fi
fi

if ((${#dropped_names[@]} > 0)); then
  if ((${#kept_names[@]} > 0)); then
    kept_text="$(IFS=,; printf '%s' "${kept_names[*]}")"
  else
    kept_text="none"
  fi
  printf 'sanitizer(s) unsupported by %s on this target dropped: %s (using: %s)\n' \
    "$compiler" \
    "$(IFS=,; printf '%s' "${dropped_names[*]}")" \
    "$kept_text" >&2
fi

if ((${#kept_names[@]} > 0)); then
  IFS=,
  printf '%s\n' "${kept_names[*]}"
else
  printf '\n'
fi
