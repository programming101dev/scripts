#!/usr/bin/env bash
# Run the p101 source-contract tools over every active wrapper library.
#
# Admitted inputs: active TUs from each library compile_commands.json, scanned
# headers, and an optional checked-in .audit-wrappers-allow file.
# Outputs: per-library wrapper-boundary, error-contract, and module-map
# reports.
# Blind spot: inactive platform sources and external library consumers are not
# treated as active code; library mode deliberately avoids closed-world API-use
# claims.

set -euo pipefail
unset CDPATH
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
# shellcheck source=shared/artifacts.sh
. ./shared/artifacts.sh

libraries_dir="../libraries"
programs_dir="../programs"
out_dir=""
facts_cache="${P101_C_FACTS_CACHE_DIR:-}"
jobs=2

usage() {
  cat <<'USAGE'
Usage: ./check-p101-library-audit.sh [options]

Run wrapper-boundary, error-contract, and module-map checks in
library mode over every lib_* repository with an existing compile database.
Build/update first.

Options:
  -l <dir>   Libraries directory. Default: ../libraries
  -p <dir>   Programs directory. Default: ../programs
  -o <dir>   Artifact directory. Default: /tmp/p101-library-audit-<pid>
  --facts-cache <dir>
             Publish content-addressed fact and instrumentation evidence.
  -j <count> Run at most this many library audits concurrently. Default: 2.
  -h         Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -l) libraries_dir="${2:?}"; shift 2 ;;
    -p) programs_dir="${2:?}"; shift 2 ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    --facts-cache) facts_cache="${2:?}"; shift 2 ;;
    -j) jobs="${2:?}"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
case "$jobs" in
  ''|*[!0-9]*|0)
    echo "Job count must be a positive integer." >&2
    exit 2
    ;;
esac

if [ -z "$out_dir" ]; then
  out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-library-audit.XXXXXX")"
fi

libraries_dir="$(CDPATH='' cd "$libraries_dir" && pwd -P)"
programs_dir="$(CDPATH='' cd "$programs_dir" && pwd -P)"
out_dir="$(mkdir -p "$out_dir" && CDPATH='' cd -P "$out_dir" && pwd -P)"
summary="$out_dir/summary.md"

find_built_tool() { p101_find_built_tool "$1" "$2"; }
find_compile_database() { p101_find_compile_database "$1"; }

wrapper_audit="${P101_AUDIT_WRAPPERS:-$programs_dir/p101-audit/audit-wrappers}"
facts_cache_tool="$PWD/checks/p101-facts-cache.py"
error_contract="${P101_AUDIT_ERRORS:-}"
module_map="${P101_AUDIT_MODULES:-}"
[ -n "$error_contract" ] || error_contract="$(find_built_tool "$programs_dir/p101-audit" audit-errors || true)"
[ -n "$module_map" ] || module_map="$(find_built_tool "$programs_dir/p101-audit" audit-modules || true)"

if [ ! -x "$wrapper_audit" ] || [ -z "$error_contract" ] || [ -z "$module_map" ]; then
  echo "Required p101 audit tools are not built. Build/update the programs first." >&2
  exit 2
fi

cat > "$summary" <<'EOF'
# p101 library source audit

| Library | Wrapper boundary | Error contract | Module structure |
| --- | --- | --- | --- |
EOF

printf 'p101 library audit output: %s\n' "$out_dir"
failed=0
found=0
failure_logs=()
results_dir="$out_dir/.results"
mkdir -p "$results_dir"

run_library_audit() {
  index="$1"
  repo="$2"
  name="$3"
  repo_out="$out_dir/$name"
  mkdir -p "$repo_out"
  result="$results_dir/$(printf '%06d' "$index").result"

  compile_db="$(find_compile_database "$repo" || true)"
  if [ -z "$compile_db" ]; then
    printf 'No compile database; build %s first.\n' "$repo" > "$repo_out/wrapper-audit.txt"
    printf '%s|FAIL|FAIL|FAIL\n' "$name" > "$result"
    return 0
  fi

  paths=("$repo/src")
  if [ -d "$repo/include" ]; then
    paths+=("$repo/include")
  fi

  wrapper_args=(--compile-db "$compile_db" --compile-db-only --facts-output "$repo_out/source-facts.tsv" --input-manifest "$repo_out/source-inputs.json" --instrumentation-output "$repo_out/instrumentation.json")
  if [ -f "$repo/.audit-wrappers-allow" ]; then
    wrapper_args+=(--allow-file "$repo/.audit-wrappers-allow")
  fi
  if [ -f "$repo/.audit-wrappers-allow.$(uname -s)" ]; then
    wrapper_args+=(--allow-file "$repo/.audit-wrappers-allow.$(uname -s)")
  fi

  wrapper_status="PASS"
  error_status="PASS"
  module_status="PASS"

  if ! "$wrapper_audit" "${wrapper_args[@]}" "${paths[@]}" > "$repo_out/wrapper-audit.txt" 2>&1; then
    wrapper_status="FAIL"
  elif [ -n "$facts_cache" ]; then
    cache_args=(
      store
      --cache "$facts_cache"
      --namespace library-full
      --producer "$wrapper_audit"
      --compile-db "$compile_db"
      --dependency-root "$libraries_dir"
      --artifact "facts=$repo_out/source-facts.tsv"
      --artifact "instrumentation=$repo_out/instrumentation.json"
    )
    for path in "${paths[@]}"; do
      cache_args+=(--path "$path")
    done
    if ! "$facts_cache_tool" "${cache_args[@]}" >> "$repo_out/wrapper-audit.txt" 2>&1; then
      wrapper_status="FAIL"
    fi
  fi

  if ! (CDPATH='' cd "$repo" && "$error_contract" -d:json -i "$repo_out/source-facts.tsv" src include) > "$repo_out/error-contract.json" 2> "$repo_out/error-contract.stderr.txt"; then
    error_status="FAIL"
  fi

  if ! (CDPATH='' cd "$repo" && "$module_map" -L -i "$repo_out/source-facts.tsv" -o "$repo_out/module-map.md" src include) > "$repo_out/module-map.stdout.txt" 2> "$repo_out/module-map.stderr.txt"; then
    module_status="FAIL"
  fi
  if ! (CDPATH='' cd "$repo" && "$module_map" -d:json -L -i "$repo_out/source-facts.tsv" -o "$repo_out/module-map.json" src include) >> "$repo_out/module-map.stdout.txt" 2>> "$repo_out/module-map.stderr.txt"; then
    module_status="FAIL"
  fi

  printf '%s|%s|%s|%s\n' \
    "$name" "$wrapper_status" "$error_status" "$module_status" > "$result"
}

repository_rows=()
governed_libraries=0
while IFS='|' read -r _url relative_path language; do
  [ -n "${relative_path:-}" ] || continue
  case "$language" in c|cxx) ;; *) continue ;; esac
  repo="$(CDPATH='' cd "$(dirname "$relative_path")" 2>/dev/null && pwd -P)/$(basename "$relative_path")"
  [ "$(dirname "$repo")" = "$libraries_dir" ] || continue
  governed_libraries=$((governed_libraries + 1))
  if [ ! -d "$repo/src" ]; then
    printf 'FAIL: governed library has no src directory: %s\n' "$repo" >&2
    printf '| %s | FAIL | FAIL | FAIL |\n' "$(basename "$repo")" >> "$summary"
    failed=1
    continue
  fi
  repository_rows+=("$repo")
done < repos.txt

found="${#repository_rows[@]}"
pids=()
index=0
for repo in "${repository_rows[@]}"; do
  name="$(basename "$repo")"
  run_library_audit "$index" "$repo" "$name" &
  pids+=("$!")
  index=$((index + 1))
  if [ "${#pids[@]}" -ge "$jobs" ]; then
    for pid in "${pids[@]}"; do
      wait "$pid" || true
    done
    pids=()
  fi
done
if [ "${#pids[@]}" -gt 0 ]; then
  for pid in "${pids[@]}"; do
    wait "$pid" || true
  done
fi

index=0
while [ "$index" -lt "$found" ]; do
  result="$results_dir/$(printf '%06d' "$index").result"
  if [ ! -f "$result" ]; then
    printf 'FAIL: library audit worker %d produced no result.\n' "$index" >&2
    failed=1
    index=$((index + 1))
    continue
  fi
  IFS='|' read -r name wrapper_status error_status module_status < "$result"
  repo_out="$out_dir/$name"
  printf '==> %-22s boundary=%s error=%s module=%s\n' "$name" "$wrapper_status" "$error_status" "$module_status"
  printf '| %s | %s | %s | %s |\n' "$name" "$wrapper_status" "$error_status" "$module_status" >> "$summary"
  if [ "$wrapper_status" != "PASS" ]; then
    failure_logs+=("$name wrapper boundary|$repo_out/wrapper-audit.txt")
    failed=1
  fi
  if [ "$error_status" != "PASS" ]; then
    failure_logs+=("$name error contract|$repo_out/error-contract.json")
    if [ -s "$repo_out/error-contract.stderr.txt" ]; then
      failure_logs+=("$name error contract stderr|$repo_out/error-contract.stderr.txt")
    fi
    failed=1
  fi
  if [ "$module_status" != "PASS" ]; then
    failure_logs+=("$name module structure|$repo_out/module-map.md")
    if [ -s "$repo_out/module-map.stderr.txt" ]; then
      failure_logs+=("$name module structure stderr|$repo_out/module-map.stderr.txt")
    fi
    failed=1
  fi
  index=$((index + 1))
done

if [ "$governed_libraries" -eq 0 ]; then
  echo "No lib_* repositories found." >&2
  exit 2
fi

if [ "$failed" -ne 0 ]; then
  printf '\nComplete failure details:\n'
  for failure_entry in "${failure_logs[@]}"; do
    failure_label="${failure_entry%%|*}"
    failure_log="${failure_entry#*|}"
    printf '%s\n' "--- $failure_label: $failure_log ---"
    if [ -f "$failure_log" ]; then
      cat "$failure_log"
    else
      printf 'missing failure artifact\n'
    fi
  done
  printf 'p101 library audit failed: %s\n' "$out_dir"
  printf 'Summary: %s\n' "$summary"
  exit 1
fi

printf 'p101 library audit passed: %s\n' "$out_dir"
printf 'Summary: %s\n' "$summary"
