#!/usr/bin/env bash
# check-p101-regression-corpus.sh — end-to-end receipts for the shared run model.

set -euo pipefail
unset CDPATH
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
# shellcheck source=shared/artifacts.sh
. ./shared/artifacts.sh

out_dir=""

usage() {
  cat <<'USAGE'
Usage: ./check-p101-regression-corpus.sh [-o <dir>]

Captures known playground behaviors once, builds one authoritative run model,
and applies the resource, synchronization, and trace policies in-process. The
policy unit suite supplies the smaller synthetic edge cases.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$out_dir" ]; then
  out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-regression-corpus.XXXXXX")"
fi
out_dir="$(mkdir -p "$out_dir" && CDPATH='' cd -P "$out_dir" && pwd -P)"
log_dir="$out_dir/logs"
mkdir -p "$log_dir"
summary="$out_dir/summary.md"

find_tool() {
  env_name="$1"
  shift
  configured="$(printenv "$env_name" 2>/dev/null || true)"
  if [ -n "$configured" ] && { [ -x "$configured" ] || command -v "$configured" >/dev/null 2>&1; }; then
    printf '%s\n' "$configured"
    return 0
  fi
  for candidate in "$@"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
    if [ -n "$candidate" ] && command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

last_build_tool() {
  p101_find_built_tool "$1" "$2"
}

observe="$(find_tool P101_INSPECT_CAPTURE \
  "$(last_build_tool ../programs/p101-inspect inspect-capture)" \
  ../programs/p101-inspect/build-clang-22/inspect-capture \
  ../programs/p101-inspect/build-clang/inspect-capture inspect-capture)"
inspect_tool="$(find_tool P101_INSPECT \
  "$(last_build_tool ../programs/p101-inspect p101-inspect)" \
  ../programs/p101-inspect/build-clang-22/p101-inspect \
  ../programs/p101-inspect/build-clang/p101-inspect p101-inspect)"
playground="$(find_tool P101_TOOL_PLAYGROUND \
  "$(last_build_tool ../playgrounds p101-tool-playground)" \
  ../playgrounds/build-clang-22/p101-tool-playground \
  ../playgrounds/build-clang/p101-tool-playground p101-tool-playground)"

cat > "$summary" <<'EOF'
# p101 regression corpus

Every runtime case uses one capture, one model build, and three policy modules.

| Status | Case | Result |
| --- | --- | --- |
EOF

check_capture_receipt() {
  receipt="$1/receipt.txt"
  [ -f "$receipt" ] &&
    grep -q '^schema=p101-run-receipt-v1$' "$receipt" &&
    grep -q '^analysis=deferred$' "$receipt" &&
    grep -q '^artifact=resources	' "$receipt" &&
    grep -q '^artifact=calls	' "$receipt"
}

check_case() {
  name="$1"
  scenario="$2"
  expectation="$3"
  expected_analysis_status="$4"
  case_root="$out_dir/$name"
  capture="$case_root/capture"
  analysis="$case_root/analysis"
  log="$log_dir/$name.log"

  echo "==> $name"
  mkdir -p "$case_root"
  set +e
  "$observe" -o "$capture" -- \
    "$playground" -s "$scenario" -o "$case_root/program-output.txt" \
    > "$log" 2>&1
  capture_status=$?
  set -e
  if [ "$capture_status" -gt 1 ] || ! check_capture_receipt "$capture"; then
    echo "    FAIL: capture status=$capture_status or invalid receipt"
    printf '| FAIL | %s | capture status=%s or invalid receipt; [log](./logs/%s) |\n' \
      "$name" "$capture_status" "$(basename "$log")" >> "$summary"
    return 1
  fi

  set +e
  "$inspect_tool" analyze -o "$analysis" "$capture" \
    >> "$log" 2>&1
  analysis_status=$?
  set -e
  if [ "$analysis_status" -ne "$expected_analysis_status" ]; then
    echo "    FAIL: analysis status=$analysis_status expected=$expected_analysis_status"
    printf '| FAIL | %s | analysis status=%s expected=%s; [log](./logs/%s) |\n' \
      "$name" "$analysis_status" "$expected_analysis_status" "$(basename "$log")" >> "$summary"
    return 1
  fi
  if ! "$inspect_tool" model verify -e "$expectation" "$analysis" >> "$log" 2>&1; then
    echo "    FAIL: executable expectation did not match"
    printf '| FAIL | %s | expectation mismatch; [log](./logs/%s) |\n' \
      "$name" "$(basename "$log")" >> "$summary"
    return 1
  fi
  if [ ! -f "$analysis/analysis-receipt.json" ]; then
    echo "    FAIL: native analysis receipt is missing"
    printf '| FAIL | %s | missing native analysis receipt; [log](./logs/%s) |\n' \
      "$name" "$(basename "$log")" >> "$summary"
    return 1
  fi

  echo "    PASS"
  printf '| PASS | %s | capture=%s analysis=%s |\n' \
    "$name" "$capture_status" "$analysis_status" >> "$summary"
}

failures=0
check_case clean tour ../playgrounds/expectations/tour.txt 0 || failures=1
check_case fd-leak fd-leak ../playgrounds/expectations/fd-leak.txt 1 || failures=1
check_case alloc-leak alloc-leak ../playgrounds/expectations/alloc-leak.txt 1 || failures=1
check_case double-close-error-path double-close ../playgrounds/expectations/double-close.txt 1 || failures=1

echo "==> policy edge-case unit corpus"
if ../programs/p101-inspect/test/test_native_cli.sh \
  "$inspect_tool" ../libraries/lib_tool_event/test/fixtures \
  >> "$log_dir/policy-unit.log" 2>&1; then
  echo "    PASS"
  printf '| PASS | policy edge cases | resource, exec, sync, and trace contracts |\n' >> "$summary"
else
  echo "    FAIL"
  printf '| FAIL | policy edge cases | [log](./logs/policy-unit.log) |\n' >> "$summary"
  failures=1
fi

echo "p101 regression corpus output: $out_dir"
echo "Summary: $summary"
exit "$failures"
