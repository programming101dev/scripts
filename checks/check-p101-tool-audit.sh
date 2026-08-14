#!/usr/bin/env bash
# check-p101-tool-audit.sh — audit the p101 tools as a teaching toolchain.
#
# This is a replayable local gate for the tool design work. It checks:
#   1. wrapper coverage for C p101 tools in strict mode;
#   2. error-object contracts for C p101 tools;
#   3. module-map design notes for C p101 tools.
#
# Wrapper-audit findings are hard failures because direct/unmapped calls make
# the observer/reporting tools silently under-report. Module-map notes are hard
# failures by default; --allow-module-notes is an explicit exploratory opt-out.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
# shellcheck source=shared/artifacts.sh
. ./shared/artifacts.sh

programs_dir="../programs"
out_dir=""
fail_module_notes=1
skip_wrapper=0
skip_error_contract=0
skip_module_map=0
facts_cache="${P101_C_FACTS_CACHE_DIR:-}"

usage() {
  cat <<'USAGE'
Usage: ./check-p101-tool-audit.sh [options]

Audit p101-* tools for strict wrapper use, error handling, and module design
notes. This script does not build tools; run the normal
build/update scripts first.

Options:
  -p <dir>              Programs directory. Default: ../programs
  -o <dir>              Artifact directory. Default: /tmp/p101-tool-audit-<pid>
  --facts-cache <dir>   Publish content-addressed Clang fact evidence.
  --allow-module-notes  Report audit-modules design notes without failing.
  --skip-wrapper        Skip strict audit-wrappers checks.
  --skip-error-contract Skip audit-errors checks.
  --skip-module-map     Skip audit-modules design-note reports.
  -h, --help            Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -p) programs_dir="${2:?}"; shift 2 ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    --facts-cache) facts_cache="${2:?}"; shift 2 ;;
    --allow-module-notes) fail_module_notes=0; shift ;;
    --skip-wrapper) skip_wrapper=1; shift ;;
    --skip-error-contract) skip_error_contract=1; shift ;;
    --skip-module-map) skip_module_map=1; shift ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$out_dir" ]; then
  out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-tool-audit.XXXXXX")"
fi

out_dir="$(mkdir -p "$out_dir" && CDPATH='' cd -P "$out_dir" && pwd -P)"
log_dir="$out_dir/logs"
mkdir -p "$log_dir"
summary="$out_dir/summary.md"
programs_dir="$(CDPATH='' cd "$programs_dir" && pwd)"
workspace_dir="$(CDPATH='' cd "$programs_dir/.." && pwd)"
wrapper_audit="${P101_AUDIT_WRAPPERS:-$programs_dir/p101-audit/audit-wrappers}"
facts_cache_tool="$workspace_dir/scripts/checks/p101-facts-cache.py"

find_built_tool() { p101_find_built_tool "$1" "$2"; }
find_compile_database() { p101_find_compile_database "$1"; }

error_contract="${P101_AUDIT_ERRORS:-}"
module_map="${P101_AUDIT_MODULES:-}"
[ -n "$error_contract" ] || error_contract="$(find_built_tool "$programs_dir/p101-audit" audit-errors || true)"
[ -n "$module_map" ] || module_map="$(find_built_tool "$programs_dir/p101-audit" audit-modules || true)"

say() {
  printf '%s\n' "$*"
}

run_logged() {
  title="$1"
  log="$2"
  shift 2

  say "==> $title"
  {
    printf '$'
    for arg in "$@"; do
      printf ' %s' "$arg"
    done
    printf '\n\n'
  } > "$log"

  if "$@" >> "$log" 2>&1; then
    say "    PASS"
    printf '| PASS | %s | [log](./logs/%s) |\n' "$title" "$(basename "$log")" >> "$summary"
  else
    say "    FAIL (see $log)"
    say "    --- failure log ---"
    sed 's/^/    | /' "$log"
    say "    --- end failure log ---"
    printf '| FAIL | %s | [log](./logs/%s) |\n' "$title" "$(basename "$log")" >> "$summary"
    return 1
  fi
}

run_module_logged() {
  title="$1"
  log="$2"
  shift 2

  say "==> $title"
  {
    printf '$'
    for arg in "$@"; do
      printf ' %s' "$arg"
    done
    printf '\n\n'
  } > "$log"

  set +e
  "$@" >> "$log" 2>&1
  command_rc=$?
  set -e

  case "$command_rc" in
    0)
      say "    PASS"
      printf '| PASS | %s | [log](./logs/%s) |\n' "$title" "$(basename "$log")" >> "$summary"
      ;;
    1)
      say "    PASS WITH FINDINGS"
      printf '| NOTE | %s | [log](./logs/%s) |\n' "$title" "$(basename "$log")" >> "$summary"
      ;;
    *)
      say "    FAIL (exit $command_rc; see $log)"
      say "    --- failure log ---"
      sed 's/^/    | /' "$log"
      say "    --- end failure log ---"
      printf '| FAIL | %s | [log](./logs/%s) |\n' "$title" "$(basename "$log")" >> "$summary"
      ;;
  esac

  return "$command_rc"
}

has_c_sources() {
  tool_dir="$1"
  find "$tool_dir" -path '*/build*' -prune -o -path '*/test/unity' -prune -o -name '*.c' -print -quit | grep -q .
}

tool_paths() {
  tool_dir="$1"
  if [ -d "$tool_dir/src" ]; then
    printf '%s\n' "$tool_dir/src"
  fi
  if [ -d "$tool_dir/include" ]; then
    printf '%s\n' "$tool_dir/include"
  fi
}

summary_counter() {
  name="$1"
  file="$2"
  awk -v key="$name" '
    /^[^:]+: outcome=/ {
      for(field_index = 1; field_index <= NF; field_index++) {
        split($field_index, field, "=")
        if(field[1] == key) {
          print field[2]
          exit
        }
      }
    }
  ' "$file"
}

write_module_notes() {
  report="$1"
  notes="$2"
  awk '
    /^## (Design|Teaching) notes$/ { in_notes = 1; next }
    /^## / && in_notes { in_notes = 0 }
    in_notes && NF { print }
  ' "$report" > "$notes"
}

notes_fixture="$out_dir/module-notes-parser-fixture.md"
notes_fixture_output="$out_dir/module-notes-parser-fixture.txt"
printf '# fixture\n\n## Teaching notes\n\n- teaching finding\n\n## Other\n\nignored\n\n## Design notes\n\n- design finding\n' > "$notes_fixture"
write_module_notes "$notes_fixture" "$notes_fixture_output"
if [ "$(grep -c 'finding$' "$notes_fixture_output" || true)" -ne 2 ]; then
  echo "Internal error: module-note parser does not recognize both supported headings." >&2
  exit 2
fi

counter_fixture="$out_dir/shared-report-counter-fixture.txt"
printf 'audit-wrappers: outcome=clean exit_status=0 findings=0 missed_wrappers=0 external_calls=0\n' > "$counter_fixture"
if [ "$(summary_counter missed_wrappers "$counter_fixture")" != "0" ] \
  || [ "$(summary_counter external_calls "$counter_fixture")" != "0" ]; then
  echo "Internal error: shared report counter parser rejected a valid summary." >&2
  exit 2
fi

cat > "$summary" <<EOF
# p101 tool audit

Workspace: \`${workspace_dir}\`

| Status | Check | Artifact |
| --- | --- | --- |
EOF

say "p101 tool audit output: $out_dir"
failed=0

if [ "$skip_wrapper" -eq 0 ]; then
  if [ ! -x "$wrapper_audit" ]; then
    say "FAIL: audit-wrappers executable not found: $wrapper_audit"
    printf '| FAIL | audit-wrappers availability | missing executable |\n' >> "$summary"
    failed=1
  else
    wrapper_tools_checked=0
    for tool_dir in "$programs_dir"/p101-*; do
      [ -d "$tool_dir" ] || continue
      if ! has_c_sources "$tool_dir"; then
        continue
      fi
      wrapper_tools_checked=$((wrapper_tools_checked + 1))

      name="$(basename "$tool_dir")"
      log="$log_dir/${name}-wrapper-audit.log"
      facts="$out_dir/${name}-source-facts.tsv"
      inputs="$out_dir/${name}-source-inputs.json"
      allow_file="$tool_dir/.audit-wrappers-allow"
      platform_allow_file="$tool_dir/.audit-wrappers-allow.$(uname -s)"
      compile_db="$(find_compile_database "$tool_dir" || true)"
      paths=()
      while IFS= read -r path; do
        paths+=("$path")
      done < <(tool_paths "$tool_dir")

      if [ -z "$compile_db" ]; then
        say "==> strict wrapper audit: $name"
        say "    FAIL (no compile database; build $tool_dir first)"
        printf '| FAIL | strict wrapper audit: %s | no compile database |\n' "$name" >> "$summary"
        failed=1
        continue
      fi

      wrapper_args=(-e --compile-db "$compile_db" --compile-db-only --facts-output "$facts" --input-manifest "$inputs")
      for foundational_library in lib_error lib_transition lib_record lib_json lib_tool_support lib_env lib_tool_event; do
        foundational_include="$workspace_dir/libraries/$foundational_library/include"
        if [ -d "$foundational_include" ]; then
          wrapper_args+=("--cflag=-I$foundational_include")
          while IFS= read -r foundational_header; do
            wrapper_args+=(--header-root "$foundational_header")
          done < <(find "$foundational_include" -type f -name '*.h' -print | LC_ALL=C sort)
        fi
      done
      if [ -f "$allow_file" ]; then
        wrapper_args+=(--allow-file "$allow_file")
      fi
      if [ -f "$platform_allow_file" ]; then
        wrapper_args+=(--allow-file "$platform_allow_file")
      fi

      if run_logged "strict wrapper audit: $name" "$log" "$wrapper_audit" "${wrapper_args[@]}" "${paths[@]}"; then
        if [ -n "$facts_cache" ]; then
          cache_args=(
            store
            --cache "$facts_cache"
            --namespace tool-full
            --producer "$wrapper_audit"
            --compile-db "$compile_db"
            --dependency-root "$workspace_dir/libraries"
            --artifact "facts=$facts"
          )
          for path in "${paths[@]}"; do
            cache_args+=(--path "$path")
          done
          if ! "$facts_cache_tool" "${cache_args[@]}" >> "$log" 2>&1; then
            say "    FAIL: $name could not publish its C-fact cache entry"
            printf '| FAIL | C-fact cache publication: %s | [log](./logs/%s) |\n' "$name" "$(basename "$log")" >> "$summary"
            failed=1
          fi
        fi
        missed="$(summary_counter missed_wrappers "$log")"
        external="$(summary_counter external_calls "$log")"
        if [ -z "$missed" ] || [ -z "$external" ]; then
          say "    FAIL: $name wrapper-audit metrics are missing"
          printf '| FAIL | strict wrapper audit metrics: %s | [log](./logs/%s) |\n' "$name" "$(basename "$log")" >> "$summary"
          failed=1
        elif [ "$missed" != "0" ] || [ "$external" != "0" ]; then
          say "    FAIL: $name missed_wrappers=${missed:-?} external_calls=${external:-?}"
          printf '| FAIL | strict wrapper audit metrics: %s | [log](./logs/%s) |\n' "$name" "$(basename "$log")" >> "$summary"
          failed=1
        fi
      else
        failed=1
      fi
    done
    if [ "$wrapper_tools_checked" -eq 0 ]; then
      say "FAIL: strict wrapper audit checked no C p101 tools"
      printf '| FAIL | strict wrapper audits | no C p101 tools discovered |\n' >> "$summary"
      failed=1
    fi
  fi
else
  say "==> strict wrapper audits"
  say "    SKIP"
  printf '| SKIP | strict wrapper audits | --skip-wrapper |\n' >> "$summary"
fi

if [ "$skip_error_contract" -eq 0 ]; then
  if [ ! -x "$error_contract" ]; then
    say "FAIL: audit-errors executable not found: $error_contract"
    printf '| FAIL | audit-errors availability | missing executable |\n' >> "$summary"
    failed=1
  else
    error_tools_checked=0
    for tool_dir in "$programs_dir"/p101-*; do
      [ -d "$tool_dir" ] || continue
      if ! has_c_sources "$tool_dir"; then
        continue
      fi
      error_tools_checked=$((error_tools_checked + 1))

      name="$(basename "$tool_dir")"
      log="$log_dir/${name}-error-contract.log"
      facts="$out_dir/${name}-source-facts.tsv"
      paths=()
      while IFS= read -r path; do
        paths+=("$path")
      done < <(tool_paths "$tool_dir")

      error_args=()
      if [ -f "$facts" ]; then
        error_args+=(-i "$facts")
      else
        compile_db="$(find_compile_database "$tool_dir" || true)"
        if [ -n "$compile_db" ]; then
          error_args+=(-C "$compile_db")
        fi
      fi
      if ! run_logged "error-contract audit: $name" "$log" "$error_contract" "${error_args[@]}" "${paths[@]}"; then
        failed=1
      fi
    done
    if [ "$error_tools_checked" -eq 0 ]; then
      say "FAIL: error-contract audit checked no C p101 tools"
      printf '| FAIL | error-contract audits | no C p101 tools discovered |\n' >> "$summary"
      failed=1
    fi
  fi
else
  say "==> error-contract audits"
  say "    SKIP"
  printf '| SKIP | error-contract audits | --skip-error-contract |\n' >> "$summary"
fi

if [ "$skip_module_map" -eq 0 ]; then
  if [ ! -x "$module_map" ]; then
    say "FAIL: audit-modules executable not found: $module_map"
    printf '| FAIL | audit-modules availability | missing executable |\n' >> "$summary"
    failed=1
  else
    module_note_total=0
    module_tools_checked=0
    for tool_dir in "$programs_dir"/p101-*; do
      [ -d "$tool_dir" ] || continue
      if ! has_c_sources "$tool_dir"; then
        continue
      fi
      module_tools_checked=$((module_tools_checked + 1))

      name="$(basename "$tool_dir")"
      report="$out_dir/${name}-module-map.md"
      log="$log_dir/${name}-module-map.log"
      notes="$out_dir/${name}-module-notes.txt"
      facts="$out_dir/${name}-source-facts.tsv"
      paths=()
      while IFS= read -r path; do
        paths+=("$path")
      done < <(tool_paths "$tool_dir")

      module_args=(-o "$report")
      if [ -f "$facts" ]; then
        module_args+=(-i "$facts")
      else
        compile_db="$(find_compile_database "$tool_dir" || true)"
        if [ -n "$compile_db" ]; then
          module_args+=(-C "$compile_db")
        fi
      fi
      if run_module_logged "module-map design report: $name" "$log" "$module_map" "${module_args[@]}" "${paths[@]}"; then
        module_rc=0
      else
        module_rc=$?
      fi
      if [ "$module_rc" -le 1 ]; then
        write_module_notes "$report" "$notes"
        note_count="$(grep -c '^- ' "$notes" || true)"
        module_note_total=$((module_note_total + note_count))
        if [ "$note_count" -gt 0 ]; then
          say "    NOTE: $name has $note_count module-map design note(s)"
          printf '| NOTE | module-map design notes: %s (%s) | [report](./%s) |\n' "$name" "$note_count" "$(basename "$report")" >> "$summary"
          if [ "$fail_module_notes" -eq 1 ]; then
            failed=1
          fi
        fi
      else
        failed=1
      fi
    done
    if [ "$module_tools_checked" -eq 0 ]; then
      say "FAIL: module-map audit checked no C p101 tools"
      printf '| FAIL | module-map design reports | no C p101 tools discovered |\n' >> "$summary"
      failed=1
    fi
    printf '\nModule-map design notes observed: `%s`\n' "$module_note_total" >> "$summary"
  fi
else
  say "==> module-map design reports"
  say "    SKIP"
  printf '| SKIP | module-map design reports | --skip-module-map |\n' >> "$summary"
fi

if [ "$failed" -ne 0 ]; then
  say "p101 tool audit failed: $out_dir"
  say "Summary: $summary"
  exit 1
fi

say "p101 tool audit passed: $out_dir"
say "Summary: $summary"
