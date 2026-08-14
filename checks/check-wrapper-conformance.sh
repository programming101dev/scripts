#!/usr/bin/env bash
# Run wrapper-library tests, normalize their event streams, and invoke the
# native p101-test conformance policy engine.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

out_dir="${TMPDIR:-/tmp}/p101-wrapper-conformance"
compiler="${CC:-cc}"
instrumentation=""
jobs="${P101_JOBS:-4}"
libraries=()

usage() {
  cat <<'USAGE'
Usage: ./checks/check-wrapper-conformance.sh --instrumentation-receipt FILE [-c CC] [-j JOBS] [-o DIR] [--library NAME]
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -c|--compiler) compiler="${2:?}"; shift 2 ;;
    -j|--jobs) jobs="${2:?}"; shift 2 ;;
    -o|--output) out_dir="${2:?}"; shift 2 ;;
    --instrumentation-receipt) instrumentation="${2:?}"; shift 2 ;;
    --library) libraries+=("${2:?}"); shift 2 ;;
    *) printf '%s:1:1: error: unknown option: %s [P101-TEST-CONFORMANCE-CLI]\n' "$0" "$1" >&2; exit 2 ;;
  esac
done

case "$jobs" in ''|*[!0-9]*) printf '%s:1:1: error: jobs must be a positive integer [P101-TEST-CONFORMANCE-CLI]\n' "$0" >&2; exit 2 ;; esac
[ "$jobs" -gt 0 ] || { printf '%s:1:1: error: jobs must be positive [P101-TEST-CONFORMANCE-CLI]\n' "$0" >&2; exit 2; }
[ -f "$instrumentation" ] || { printf '%s:1:1: error: instrumentation receipt is missing: %s [P101-TEST-CONFORMANCE-CLI]\n' "$0" "$instrumentation" >&2; exit 2; }

find_tool() {
  environment_name="$1"
  repository="$2"
  executable="$3"
  configured="${!environment_name:-}"
  if [ -n "$configured" ] && [ -x "$configured" ]; then
    printf '%s\n' "$configured"
    return 0
  fi
  if [ -f "$repository/.last-build-dir" ]; then
    build_name="$(sed -n '1p' "$repository/.last-build-dir")"
    if [ -x "$repository/$build_name/$executable" ]; then
      printf '%s\n' "$repository/$build_name/$executable"
      return 0
    fi
  fi
  find "$repository" -maxdepth 2 -type f -name "$executable" -perm -u+x 2>/dev/null | sort | tail -1
}

policy="$(find_tool P101_TEST_WRAPPER_CONFORMANCE ../programs/p101-test test-wrapper-conformance)"
event_model="$(find_tool P101_EVENT_MODEL ../libraries/lib_tool_event p101-event-model)"
[ -x "$policy" ] || { printf '%s:1:1: error: native conformance policy engine is not built [P101-TEST-CONFORMANCE-CLI]\n' "$0" >&2; exit 2; }
[ -x "$event_model" ] || { printf '%s:1:1: error: p101-event-model is not built [P101-TEST-CONFORMANCE-CLI]\n' "$0" >&2; exit 2; }

case "$(uname -s)" in
  Darwin) platform=macos ;;
  Linux) platform=linux ;;
  FreeBSD) platform=freebsd ;;
  *) printf '%s:1:1: error: unsupported host platform [P101-TEST-CONFORMANCE-CLI]\n' "$0" >&2; exit 2 ;;
esac

if [ "${#libraries[@]}" -eq 0 ]; then
  while IFS= read -r manifest; do
    libraries+=("$(basename "$(dirname "$manifest")")")
  done < <(find ../libraries -mindepth 2 -maxdepth 2 -name api-manifest.tsv -type f | sort)
fi

mkdir -p "$out_dir/results"
out_dir="$(CDPATH='' cd -P "$out_dir" && pwd -P)"
instrumentation="$(CDPATH='' cd -P "$(dirname "$instrumentation")" && printf '%s/%s\n' "$PWD" "$(basename "$instrumentation")")"

headers="$out_dir/platform-headers.c"
macros="$out_dir/platform-macros.txt"
: > "$headers"
for library in "${libraries[@]}"; do
  manifest="../libraries/$library/test/fault-outcome-manifest.tsv"
  [ -f "$manifest" ] || { printf '%s:1:1: error: missing generated fault manifest [P101-TEST-CONFORMANCE-CLI]\n' "$manifest" >&2; exit 2; }
  awk -F '\t' 'NR > 1 && !seen[$4]++ { print "#include <" $4 ">" }' "$manifest" >> "$headers"
done
sort -u "$headers" -o "$headers"
if ! "$compiler" -dM -E -x c "$headers" > "$macros"; then
  printf '%s:1:1: error: cannot inspect active platform fault symbols [P101-TEST-CONFORMANCE-CLI]\n' "$compiler" >&2
  exit 2
fi

run_library() {
  library="$1"
  repo="$(CDPATH='' cd -P "../libraries/$library" && pwd -P)"
  calls="$out_dir/$library.calls.log"
  resources="$out_dir/$library.resources.log"
  outcomes="$out_dir/$library.outcomes.tsv"
  model="$out_dir/$library.run-model.json"
  test_log="$out_dir/$library.test.log"
  model_log="$out_dir/$library.model.log"
  policy_log="$out_dir/$library.policy.log"
  receipt="$out_dir/results/$library.json"
  result="$out_dir/results/$library.result"
  rm -f "$calls" "$resources" "$outcomes" "$model" "$receipt"
  set +e
  (
    cd "$repo"
    P101_EVENT_RUN_ID="p101-wrapper-conformance-${library//_/-}" \
    P101_WRAPPER_CONFORMANCE=1 \
    P101_CALL_LOG="$calls" \
    P101_CALL_LOG_ARGS=1 \
    P101_CALL_LOG_RESULT=1 \
    P101_RESOURCE_LOG="$resources" \
    P101_WRAPPER_OUTCOME_LOG="$outcomes" \
    ./test.sh
  ) > "$test_log" 2>&1
  test_status=$?
  set -e
  if [ "$test_status" -ne 0 ]; then
    printf '%s\tFAIL\t0\t0\t0\t%s\n' "$library" "$test_status" > "$result"
    return
  fi
  [ -f "$resources" ] || : > "$resources"
  if ! "$event_model" -r "$resources" -c "$calls" -o "$model" > "$model_log" 2>&1; then
    printf '%s\tFAIL\t0\t0\t0\t2\n' "$library" > "$result"
    return
  fi
  set +e
  "$policy" --library "$library" --repo "$repo" \
    --instrumentation "$instrumentation" --model "$model" \
    --outcomes "$outcomes" --platform "$platform" --macros "$macros" \
    --receipt "$receipt" > "$policy_log" 2>&1
  policy_status=$?
  set -e
  if [ ! -s "$receipt" ]; then
    printf '%s\tFAIL\t0\t0\t0\t%s\n' "$library" "$policy_status" > "$result"
    return
  fi
  apis="$(sed -n 's/.*"apis":\([0-9][0-9]*\).*/\1/p' "$receipt")"
  fault_cases="$(sed -n 's/.*"fault_cases":\([0-9][0-9]*\).*/\1/p' "$receipt")"
  observed="$(sed -n 's/.*"fault_outcomes_observed":\([0-9][0-9]*\).*/\1/p' "$receipt")"
  if [ "$policy_status" -eq 0 ]; then status=PASS; else status=FAIL; fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$library" "$status" "${apis:-0}" "${fault_cases:-0}" "${observed:-0}" "$policy_status" > "$result"
}

running=0
for library in "${libraries[@]}"; do
  run_library "$library" &
  running=$((running + 1))
  if [ "$running" -ge "$jobs" ]; then
    wait
    running=0
  fi
done
wait

unit_evidence="$out_dir/unit-tests.tsv"
printf 'library\tstatus\n' > "$unit_evidence"
for result in "$out_dir"/results/*.result; do
  awk -F '\t' '{ print $1 "\t" $2 }' "$result" >> "$unit_evidence"
done

public_apis="$(awk -F '\t' '{ total += $3 } END { print total + 0 }' "$out_dir"/results/*.result)"
fault_cases="$(awk -F '\t' '{ total += $4 } END { print total + 0 }' "$out_dir"/results/*.result)"
observed="$(awk -F '\t' '{ total += $5 } END { print total + 0 }' "$out_dir"/results/*.result)"
failed="$(awk -F '\t' '$2 != "PASS" { count++ } END { print count + 0 }' "$out_dir"/results/*.result)"

receipt="$out_dir/receipt.json"
{
  printf '{"schema":"p101-wrapper-conformance-receipt-v4","platform":"%s","public_apis":%s,"fault_cases":%s,"fault_outcomes_observed":%s,"libraries":[' "$platform" "$public_apis" "$fault_cases" "$observed"
  separator=""
  for fragment in "$out_dir"/results/*.json; do
    [ -s "$fragment" ] || continue
    printf '%s' "$separator"
    tr -d '\n' < "$fragment"
    separator=,
  done
  printf '],"passed":%s}\n' "$([ "$failed" -eq 0 ] && printf true || printf false)"
} > "$receipt"

printf 'wrapper conformance: %s APIs, %s/%s direct platform fault outcomes, %s libraries\n' "$public_apis" "$observed" "$fault_cases" "${#libraries[@]}"
if [ "$failed" -ne 0 ]; then
  for result in "$out_dir"/results/*.result; do
    IFS=$'\t' read -r library status _apis _faults _observed phase_status < "$result"
    [ "$status" = PASS ] && continue
    printf 'FAIL: %s (exit %s)\n' "$library" "$phase_status"
    for log in "$out_dir/$library.test.log" "$out_dir/$library.model.log" "$out_dir/$library.policy.log"; do
      [ -s "$log" ] || continue
      printf '%s:1:1: note: complete failure output follows\n' "$log"
      sed 's/^/  | /' "$log"
    done
  done
  exit 1
fi
printf 'wrapper conformance passed: %s\n' "$receipt"
