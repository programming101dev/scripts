#!/usr/bin/env bash
# Run the p101 source-contract tools over every active wrapper library.
#
# Admitted inputs: active TUs from each library compile_commands.json, scanned
# headers, and an optional checked-in .p101-wrapper-audit-allow file.
# Outputs: per-library wrapper-boundary, wrapper-form, error-contract, and
# module-map reports.
# Blind spot: inactive platform sources and external library consumers are not
# treated as active code; library mode deliberately avoids closed-world API-use
# claims.

set -euo pipefail
unset CDPATH
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

libraries_dir="../libraries"
programs_dir="../programs"
out_dir=""

usage() {
  cat <<'USAGE'
Usage: ./check-p101-library-audit.sh [options]

Run wrapper-boundary, wrapper-form, error-contract, and module-map checks in
library mode over every lib_* repository with an existing compile database.
Build/update first.

Options:
  -l <dir>   Libraries directory. Default: ../libraries
  -p <dir>   Programs directory. Default: ../programs
  -o <dir>   Artifact directory. Default: /tmp/p101-library-audit-<pid>
  -h         Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -l) libraries_dir="${2:?}"; shift 2 ;;
    -p) programs_dir="${2:?}"; shift 2 ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$out_dir" ]; then
  out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-library-audit.XXXXXX")"
fi

libraries_dir="$(CDPATH='' cd "$libraries_dir" && pwd -P)"
programs_dir="$(CDPATH='' cd "$programs_dir" && pwd -P)"
out_dir="$(mkdir -p "$out_dir" && CDPATH='' cd -P "$out_dir" && pwd -P)"
summary="$out_dir/summary.md"

find_built_tool() {
  repo="$1"
  name="$2"

  if [ -f "$repo/.last-build-dir" ]; then
    build_dir="$(cat "$repo/.last-build-dir")"
    case "$build_dir" in
      *coverage*|*profile*) ;;
      *)
        if [ -x "$repo/$build_dir/$name" ]; then
          printf '%s\n' "$repo/$build_dir/$name"
          return 0
        fi
        ;;
    esac
  fi

  # Prefer conventional non-instrumented builds before version-suffixed build
  # directories. Shell glob ordering can otherwise select an older
  # build-clang-<version> binary ahead of a freshly rebuilt build-clang tool.
  for candidate in "$repo"/build/"$name" "$repo"/build-clang/"$name" "$repo"/build-gcc/"$name" "$repo"/build-*/"$name"; do
    case "$candidate" in
      *coverage*|*profile*) ;;
      *)
        if [ -x "$candidate" ]; then
          printf '%s\n' "$candidate"
          return 0
        fi
        ;;
    esac
  done

  command -v "$name" 2>/dev/null
}

find_compile_database() {
  repo="$1"

  if [ -f "$repo/.last-build-dir" ]; then
    build_dir="$(cat "$repo/.last-build-dir")"
    if [ -f "$repo/$build_dir/compile_commands.json" ]; then
      printf '%s\n' "$repo/$build_dir/compile_commands.json"
      return 0
    fi
  fi

  for candidate in "$repo"/build-*/compile_commands.json "$repo"/build/compile_commands.json; do
    if [ -f "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

wrapper_audit="$programs_dir/p101-wrapper-audit/p101-wrapper-audit"
error_contract="$(find_built_tool "$programs_dir/p101-error-contract" p101-error-contract || true)"
module_map="$(find_built_tool "$programs_dir/p101-module-map" p101-module-map || true)"

if [ ! -x "$wrapper_audit" ] || [ -z "$error_contract" ] || [ -z "$module_map" ]; then
  echo "Required p101 audit tools are not built. Build/update the programs first." >&2
  exit 2
fi

cat > "$summary" <<'EOF'
# p101 library source audit

| Library | Wrapper boundary | Wrapper form | Error contract | Module structure |
| --- | --- | --- | --- | --- |
EOF

printf 'p101 library audit output: %s\n' "$out_dir"
failed=0
found=0

while IFS='|' read -r _url relative_path language; do
  [ -n "${relative_path:-}" ] || continue
  case "$language" in c|cxx) ;; *) continue ;; esac
  repo="$(CDPATH='' cd "$(dirname "$relative_path")" 2>/dev/null && pwd -P)/$(basename "$relative_path")"
  [ "$(dirname "$repo")" = "$libraries_dir" ] || continue
  [ -d "$repo/src" ] || continue
  name="$(basename "$repo")"
  repo_out="$out_dir/$name"
  mkdir -p "$repo_out"
  found=$((found + 1))

  compile_db="$(find_compile_database "$repo" || true)"
  if [ -z "$compile_db" ]; then
    printf '==> %-22s FAIL (no compile database)\n' "$name"
    printf '| %s | FAIL | FAIL | FAIL | FAIL |\n' "$name" >> "$summary"
    failed=1
    continue
  fi

  paths=("$repo/src")
  if [ -d "$repo/include" ]; then
    paths+=("$repo/include")
  fi

  wrapper_args=(--compile-db "$compile_db" --compile-db-only --facts-output "$repo_out/source-facts.tsv" --input-manifest "$repo_out/source-inputs.json")
  if [ -f "$repo/.p101-wrapper-audit-allow" ]; then
    wrapper_args+=(--allow-file "$repo/.p101-wrapper-audit-allow")
  fi

  wrapper_status="PASS"
  form_status="N/A"
  error_status="PASS"
  module_status="PASS"

  if ! "$wrapper_audit" "${wrapper_args[@]}" "${paths[@]}" > "$repo_out/wrapper-audit.txt" 2>&1; then
    wrapper_status="FAIL"
    failed=1
  fi

  if [ -f "$repo/wrapper-form-contract.json" ]; then
    form_status="PASS"
    if ! "$wrapper_audit" \
      --compile-db "$compile_db" \
      --compile-db-only \
      --wrapper-form-contract "$repo/wrapper-form-contract.json" \
      --wrapper-form-only \
      "$repo" > "$repo_out/wrapper-form.txt" 2>&1; then
      form_status="FAIL"
      failed=1
    fi
  fi

  if ! (CDPATH='' cd "$repo" && "$error_contract" -j -i "$repo_out/source-facts.tsv" src include) > "$repo_out/error-contract.json" 2> "$repo_out/error-contract.stderr.txt"; then
    error_status="FAIL"
    failed=1
  fi

  if ! (CDPATH='' cd "$repo" && "$module_map" -L -i "$repo_out/source-facts.tsv" -o "$repo_out/module-map.md" src include) > "$repo_out/module-map.stdout.txt" 2> "$repo_out/module-map.stderr.txt"; then
    module_status="FAIL"
    failed=1
  fi
  if ! (CDPATH='' cd "$repo" && "$module_map" -j -L -i "$repo_out/source-facts.tsv" -o "$repo_out/module-map.json" src include) >> "$repo_out/module-map.stdout.txt" 2>> "$repo_out/module-map.stderr.txt"; then
    module_status="FAIL"
    failed=1
  fi

  printf '==> %-22s boundary=%s form=%s error=%s module=%s\n' "$name" "$wrapper_status" "$form_status" "$error_status" "$module_status"
  printf '| %s | %s | %s | %s | %s |\n' "$name" "$wrapper_status" "$form_status" "$error_status" "$module_status" >> "$summary"
done < repos.txt

if [ "$found" -eq 0 ]; then
  echo "No lib_* repositories found." >&2
  exit 2
fi

if [ "$failed" -ne 0 ]; then
  printf 'p101 library audit failed: %s\n' "$out_dir"
  printf 'Summary: %s\n' "$summary"
  exit 1
fi

printf 'p101 library audit passed: %s\n' "$out_dir"
printf 'Summary: %s\n' "$summary"
