#!/usr/bin/env bash
# check-p101-tool-audit.sh — audit the p101 tools as a teaching toolchain.
#
# This is a replayable local gate for the tool design work. It checks:
#   1. README/tool-contract minimums for every p101-* tool;
#   2. wrapper coverage for C p101 tools in strict mode;
#   3. module-map design notes for C p101 tools.
#
# Wrapper-audit findings are hard failures because direct/unmapped calls make
# the observer/reporting tools silently under-report. Module-map notes are
# teaching-design hints by default; use --fail-module-notes when doing a strict
# cleanup ratchet.

set -euo pipefail
CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

programs_dir="../programs"
out_dir=""
fail_module_notes=0
skip_contracts=0
skip_wrapper=0
skip_module_map=0

usage() {
  cat <<'USAGE'
Usage: ./check-p101-tool-audit.sh [options]

Audit p101-* tools for README contracts, strict wrapper use, and module design
notes. This script does not build tools; run the normal build/update scripts
first.

Options:
  -p <dir>              Programs directory. Default: ../programs
  -o <dir>              Artifact directory. Default: /tmp/p101-tool-audit-<pid>
  --fail-module-notes   Treat p101-module-map design notes as failures.
  --skip-contracts      Skip README/tool-contract checks.
  --skip-wrapper        Skip strict p101-wrapper-audit checks.
  --skip-module-map     Skip p101-module-map design-note reports.
  -h, --help            Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -p) programs_dir="${2:?}"; shift 2 ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    --fail-module-notes) fail_module_notes=1; shift ;;
    --skip-contracts) skip_contracts=1; shift ;;
    --skip-wrapper) skip_wrapper=1; shift ;;
    --skip-module-map) skip_module_map=1; shift ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$out_dir" ]; then
  out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-tool-audit.XXXXXX")"
fi

out_dir="$(mkdir -p "$out_dir" && CDPATH= cd -P "$out_dir" && pwd -P)"
log_dir="$out_dir/logs"
mkdir -p "$log_dir"
summary="$out_dir/summary.md"
programs_dir="$(CDPATH= cd "$programs_dir" && pwd)"
workspace_dir="$(CDPATH= cd "$programs_dir/.." && pwd)"
wrapper_audit="$programs_dir/p101-wrapper-audit/p101-wrapper-audit"
module_map="$programs_dir/p101-module-map/build-clang/p101-module-map"

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
    printf '| FAIL | %s | [log](./logs/%s) |\n' "$title" "$(basename "$log")" >> "$summary"
    return 1
  fi
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

metric_value() {
  name="$1"
  file="$2"
  awk -F': *' -v key="$name" '$1 == key { print $2; exit }' "$file"
}

write_module_notes() {
  report="$1"
  notes="$2"
  awk '
    /^## Design notes$/ { in_notes = 1; next }
    /^## / && in_notes { in_notes = 0 }
    in_notes && NF { print }
  ' "$report" > "$notes"
}

cat > "$summary" <<EOF
# p101 tool audit

Workspace: \`${workspace_dir}\`

| Status | Check | Artifact |
| --- | --- | --- |
EOF

say "p101 tool audit output: $out_dir"
failed=0

if [ "$skip_contracts" -eq 0 ]; then
  if ! run_logged "p101 README/tool contract checks" "$log_dir/check-p101-tool-contracts.log" "$workspace_dir/scripts/check-p101-tool-contracts.sh" -p "$programs_dir"; then
    failed=1
  fi
else
  say "==> p101 README/tool contract checks"
  say "    SKIP"
  printf '| SKIP | p101 README/tool contract checks | --skip-contracts |\n' >> "$summary"
fi

if [ "$skip_wrapper" -eq 0 ]; then
  if [ ! -x "$wrapper_audit" ]; then
    say "FAIL: p101-wrapper-audit executable not found: $wrapper_audit"
    printf '| FAIL | p101-wrapper-audit availability | missing executable |\n' >> "$summary"
    failed=1
  else
    for tool_dir in "$programs_dir"/p101-*; do
      [ -d "$tool_dir" ] || continue
      if ! has_c_sources "$tool_dir"; then
        continue
      fi

      name="$(basename "$tool_dir")"
      log="$log_dir/${name}-wrapper-audit.log"
      paths=()
      while IFS= read -r path; do
        paths+=("$path")
      done < <(tool_paths "$tool_dir")

      if run_logged "strict wrapper audit: $name" "$log" "$wrapper_audit" -e "${paths[@]}"; then
        missed="$(metric_value missed_wrappers "$log")"
        external="$(metric_value external_calls "$log")"
        if [ "${missed:-0}" != "0" ] || [ "${external:-0}" != "0" ]; then
          say "    FAIL: $name missed_wrappers=${missed:-?} external_calls=${external:-?}"
          printf '| FAIL | strict wrapper audit metrics: %s | [log](./logs/%s) |\n' "$name" "$(basename "$log")" >> "$summary"
          failed=1
        fi
      else
        failed=1
      fi
    done
  fi
else
  say "==> strict wrapper audits"
  say "    SKIP"
  printf '| SKIP | strict wrapper audits | --skip-wrapper |\n' >> "$summary"
fi

if [ "$skip_module_map" -eq 0 ]; then
  if [ ! -x "$module_map" ]; then
    say "FAIL: p101-module-map executable not found: $module_map"
    printf '| FAIL | p101-module-map availability | missing executable |\n' >> "$summary"
    failed=1
  else
    module_note_total=0
    for tool_dir in "$programs_dir"/p101-*; do
      [ -d "$tool_dir" ] || continue
      if ! has_c_sources "$tool_dir"; then
        continue
      fi

      name="$(basename "$tool_dir")"
      report="$out_dir/${name}-module-map.md"
      log="$log_dir/${name}-module-map.log"
      notes="$out_dir/${name}-module-notes.txt"
      paths=()
      while IFS= read -r path; do
        paths+=("$path")
      done < <(tool_paths "$tool_dir")

      if run_logged "module-map design report: $name" "$log" "$module_map" -F "$wrapper_audit" -o "$report" "${paths[@]}"; then
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
