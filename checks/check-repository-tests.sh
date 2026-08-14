#!/usr/bin/env bash
# Run each repository-owned unit suite and a bounded fuzz smoke where supported.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
# shellcheck source=shared/compilers.sh
. ./shared/compilers.sh

out_dir=""
fuzz_secs=5
skip_fuzz=0
c_compiler=""
cxx_compiler=""
jobs="${P101_JOBS:-0}"
unit_evidence_files=()

usage() {
  cat <<'USAGE'
Usage: ./check-repository-tests.sh [-c <cc>] [-x <cxx>] [-o <dir>] [-j <jobs>] [--unit-evidence <tsv>] [--fuzz-secs <seconds>] [--skip-fuzz]

Runs every standalone test.sh named by repos.txt, plus local p101-* program
repositories that have not yet been added to that manifest. Repositories with a
fuzz target also receive a bounded fuzz run when a fuzzer-capable compiler is
available. A missing test suite is reported as NO TEST rather than silently
treated as tested.
The --fuzz-secs default is 5. A value of 0 is intentionally unbounded; use
--skip-fuzz when no fuzz execution is wanted.
When -c/-x is supplied, unit tests and fuzzing use those compilers. This keeps
test executables, fuzz executables, and sanitizer-instrumented p101 libraries
on the same runtime.

Repositories run concurrently with a conservative host-derived default capped
at two workers. Use -j 1 for serial execution. P101_JOBS supplies the default
when -j is omitted.

--unit-evidence accepts the checked unit-tests.tsv emitted by executable
wrapper conformance. A PASS for a library reuses that stronger test.sh run;
fuzzing and repositories absent from the receipt still execute here.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -c) c_compiler="${2:?}"; shift 2 ;;
    -x) cxx_compiler="${2:?}"; shift 2 ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    -j|--jobs) jobs="${2:?}"; shift 2 ;;
    --unit-evidence) unit_evidence_files+=("${2:?}"); shift 2 ;;
    --fuzz-secs) fuzz_secs="${2:?}"; shift 2 ;;
    --skip-fuzz) skip_fuzz=1; shift ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

resolve_compiler() {
  requested="$1"
  [ -n "$requested" ] || return 0
  p101_resolve_compiler "$requested" compiler_paths.txt
}

c_compiler="$(resolve_compiler "$c_compiler")"
cxx_compiler="$(resolve_compiler "$cxx_compiler")"

case "$fuzz_secs" in *[!0-9]*|'') echo "--fuzz-secs must be an unsigned integer" >&2; exit 2 ;; esac
case "$jobs" in *[!0-9]*|'') echo "--jobs must be a positive integer" >&2; exit 2 ;; esac
if [ "$jobs" -eq 0 ]; then
  jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)"
  case "$jobs" in *[!0-9]*|'') jobs="$(sysctl -n hw.ncpu 2>/dev/null || printf '1\n')" ;; esac
  case "$jobs" in *[!0-9]*|'') jobs=1 ;; esac
  [ "$jobs" -le 2 ] || jobs=2
fi
[ "$jobs" -gt 0 ] || { echo "--jobs must be a positive integer" >&2; exit 2; }
[ -n "$out_dir" ] || out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-repository-tests.XXXXXX")"
out_dir="$(mkdir -p "$out_dir" && CDPATH='' cd -P "$out_dir" && pwd -P)"
summary="$out_dir/summary.md"
results_dir="$out_dir/results"
cost_contract="contracts/repository-test-costs.tsv"
mkdir -p "$results_dir"
printf '# p101 standalone repository tests\n\n| Repository | Unit tests | Fuzz smoke | Seconds |\n| --- | --- | --- | ---: |\n' > "$summary"

if [ "${#unit_evidence_files[@]}" -gt 0 ]; then
  for unit_evidence in "${unit_evidence_files[@]}"; do
    [ -f "$unit_evidence" ] || {
      printf 'Unit-test evidence is missing: %s\n' "$unit_evidence" >&2
      exit 2
    }
    awk -F '\t' '
      NR == 1 { valid = ($1 == "library" && $2 == "status"); next }
      NF != 2 || $1 == "" || ($2 != "PASS" && $2 != "FAIL") || seen[$1]++ {
        valid = 0
      }
      END { exit valid ? 0 : 1 }
    ' "$unit_evidence" || {
      printf 'Unit-test evidence is malformed: %s\n' "$unit_evidence" >&2
      exit 2
    }
  done
fi

has_reusable_unit_evidence() {
  evidence_name="$1"
  [ "${#unit_evidence_files[@]}" -gt 0 ] || return 1
  for evidence_path in "${unit_evidence_files[@]}"; do
    if awk -F '\t' -v name="$evidence_name" \
      '$1 == name && $2 == "FAIL" { found=1 } END { exit !found }' \
      "$evidence_path"; then
      return 1
    fi
  done
  for evidence_path in "${unit_evidence_files[@]}"; do
    if awk -F '\t' -v name="$evidence_name" \
      '$1 == name && $2 == "PASS" { found=1 } END { exit !found }' \
      "$evidence_path"; then
      return 0
    fi
  done
  return 1
}

[ -f "$cost_contract" ] || {
  printf 'Repository test cost contract is missing: %s\n' "$cost_contract" >&2
  exit 2
}
if ! awk -F '|' '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    NF != 2 || $1 == "" || $2 !~ /^[1-9][0-9]*$/ || seen[$1]++ {
      printf "%s:%d: invalid repository cost row: %s\n", FILENAME, NR, $0 > "/dev/stderr"
      invalid=1
    }
    $1 == "*" { defaults++ }
    END {
      if(defaults != 1) {
        printf "%s: expected exactly one explicit * default cost, found %d\n",
               FILENAME, defaults > "/dev/stderr"
        invalid=1
      }
      exit invalid ? 1 : 0
    }
  ' "$cost_contract"; then
  exit 2
fi
default_cost="$(awk -F'|' '$1 == "*" { print $2; exit }' "$cost_contract")"

run_repository() {
  index="$1"
  relative="$2"
  language="$3"
  started="$SECONDS"
  result="$results_dir/$(printf '%06d' "$index").result"
  repo="$(CDPATH='' cd "$relative" 2>/dev/null && pwd -P || true)"
  if [ -z "$repo" ]; then
    printf '%s|MISSING|MISSING|||%s\n' "$relative" "$((SECONDS - started))" > "$result"
    return
  fi

  name="$(basename "$repo")"
  unit="NO TEST"
  fuzz="NO FUZZ TARGET"
  test_log="$out_dir/$name-test.log"
  fuzz_log="$out_dir/$name-fuzz.log"

  # C/C++ repositories receive the shared test launcher even when they do not
  # yet own a test tree. Python tools use repository-specific test launchers.
  # Keep those cases distinct so a copied launcher is not mistaken for a suite.
  has_unit_suite=0
  if [ -f "$repo/test/CMakeLists.txt" ] || [ "$language" = "python" ]; then
    has_unit_suite=1
  fi
  if [ "$has_unit_suite" -eq 1 ] && \
     has_reusable_unit_evidence "$name"; then
    unit="REUSED"
    printf 'Reused stricter wrapper-conformance test.sh evidence for %s.\n' \
      "$name" > "$test_log"
  elif [ "$has_unit_suite" -eq 1 ] && [ -x "$repo/test.sh" ]; then
    if (CDPATH='' cd "$repo" && P101_TEST_CC="$c_compiler" P101_TEST_CXX="$cxx_compiler" ./test.sh) > "$test_log" 2>&1; then
      unit="PASS"
    else
      unit="FAIL"
    fi
  elif [ "$has_unit_suite" -eq 1 ]; then
    unit="MISSING"
    printf 'Declared unit suite has no executable test.sh: %s\n' "$repo" \
      > "$test_log"
  fi

  if [ "$skip_fuzz" -eq 1 ]; then
    fuzz="SKIP"
  elif [ -x "$repo/fuzz.sh" ] && [ -f "$repo/fuzz/CMakeLists.txt" ]; then
    fuzz_compiler="$c_compiler"
    case "$language" in cxx|CXX|CPP) fuzz_compiler="$cxx_compiler" ;; esac
    fuzz_command=(./fuzz.sh)
    if [ -n "$fuzz_compiler" ]; then
      fuzz_command=(env "FUZZ_CC=$fuzz_compiler" ./fuzz.sh)
    fi
    if (CDPATH='' cd "$repo" && "${fuzz_command[@]}" --can-fuzz) > "$fuzz_log" 2>&1; then
      if (CDPATH='' cd "$repo" && "${fuzz_command[@]}" -t "$fuzz_secs") > "$fuzz_log" 2>&1; then
        fuzz="PASS"
      else
        fuzz="FAIL"
      fi
    else
      fuzz="UNAVAILABLE"
    fi
  fi

  printf '%s|%s|%s|%s|%s|%s\n' "$name" "$unit" "$fuzz" "$test_log" "$fuzz_log" "$((SECONDS - started))" > "$result"
}

run_repository_guarded() {
  index="$1"
  relative="$2"
  started="$SECONDS"
  result="$results_dir/$(printf '%06d' "$index").result"
  name="$(basename "$relative")"

  # Background functions inherit errexit. Guarantee one result record even
  # when an unexpected command escapes the explicitly handled test/fuzz
  # branches; otherwise the aggregate can finish with a misleading hole.
  set +e
  run_repository "$@"
  status=$?
  if [ ! -f "$result" ]; then
    printf '%s|FAIL|UNAVAILABLE|%s||%s\n' \
      "$name" \
      "$out_dir/$name-test.log" \
      "$((SECONDS - started))" > "$result"
  fi
  return "$status"
}

wait_for_any() {
  while :; do
    worker_index=0
    while [ "$worker_index" -lt "${#worker_pids[@]}" ]; do
      worker_pid="${worker_pids[$worker_index]}"
      if ! kill -0 "$worker_pid" 2>/dev/null; then
        wait "$worker_pid" || true
        if [ "$worker_count" -eq 1 ]; then
          worker_pids=()
        else
          unset 'worker_pids[worker_index]'
          worker_pids=("${worker_pids[@]}")
        fi
        worker_count=$((worker_count - 1))
        return
      fi
      worker_index=$((worker_index + 1))
    done
    sleep 0.05
  done
}

worker_pids=()
worker_count=0
repository_count=0
worklist="$results_dir/worklist.tsv"
: > "$worklist"
while IFS='|' read -r _url relative language || [ -n "${relative:-}" ]; do
  [ -n "${relative:-}" ] || continue
  [ "$language" != "c-bootstrap" ] || continue
  name="$(basename "$relative")"
  configured_cost="$(awk -F'|' -v name="$name" '$1 == name { print $2; exit }' "$cost_contract")"
  cost="${configured_cost:-$default_cost}"
  printf '%s|%s|%s|%s\n' "$repository_count" "$relative" "$language" "$cost" >> "$worklist"
  repository_count=$((repository_count + 1))
done < <(
  cat repos.txt
  for local_repo in ../programs/p101-*; do
    [ -d "$local_repo" ] || continue
    if ! awk -F'|' -v path="$local_repo" '$2 == path { found=1 } END { exit !found }' repos.txt; then
      if [ -f "$local_repo/CMakeLists.txt" ]; then
        printf '|%s|c\n' "$local_repo"
      else
        printf '|%s|python\n' "$local_repo"
      fi
    fi
  done
)

while IFS='|' read -r repository_index relative language _cost; do
  # Workers must not inherit the worklist stream. A repository test that reads
  # stdin would otherwise consume later work items and create missing receipts.
  run_repository_guarded "$repository_index" "$relative" "$language" </dev/null &
  worker_pids+=("$!")
  worker_count=$((worker_count + 1))
  if [ "$worker_count" -ge "$jobs" ]; then
    wait_for_any
  fi
done < <(sort -t '|' -k4,4nr -k1,1n "$worklist")

while [ "$worker_count" -gt 0 ]; do
  wait_for_any
done

failed=0
fuzz_unavailable=0
failure_logs=()
index=0
while [ "$index" -lt "$repository_count" ]; do
  result="$results_dir/$(printf '%06d' "$index").result"
  if [ ! -f "$result" ]; then
    printf 'FAIL: repository worker %d did not produce a result\n' "$index" >&2
    failed=1
    index=$((index + 1))
    continue
  fi
  IFS='|' read -r name unit fuzz test_log fuzz_log duration < "$result"
  printf '%-30s test=%-8s fuzz=%-14s seconds=%s\n' "$name" "$unit" "$fuzz" "$duration"
  printf '| %s | %s | %s | %s |\n' "$name" "$unit" "$fuzz" "$duration" >> "$summary"
  if [ "$unit" = "FAIL" ] || [ "$unit" = "MISSING" ]; then
    failure_logs+=("$name unit tests|$test_log")
    failed=1
  fi
  if [ "$fuzz" = "FAIL" ]; then
    failure_logs+=("$name fuzz smoke|$fuzz_log")
    failed=1
  elif [ "$fuzz" = "UNAVAILABLE" ]; then
    fuzz_unavailable=$((fuzz_unavailable + 1))
  fi
  index=$((index + 1))
done

if [ "$failed" -ne 0 ]; then
  printf '\nComplete failure details:\n'
  for failure_entry in "${failure_logs[@]}"; do
    failure_label="${failure_entry%%|*}"
    failure_log="${failure_entry#*|}"
    printf '%s\n' "--- $failure_label: $failure_log ---"
    if [ -f "$failure_log" ]; then
      cat "$failure_log"
    else
      printf 'missing failure log\n'
    fi
    printf '%s\n' "--- end $failure_label ---"
  done
fi

printf 'Repository test workers: %s\n' "$jobs"
printf 'Repository fuzz targets unavailable on this toolchain: %s\n' \
  "$fuzz_unavailable"
printf 'Repository test summary: %s\n' "$summary"
receipt_writer=${P101_TEST_REPOSITORY_RECEIPT:-}
if [ -z "$receipt_writer" ]; then
  receipt_writer=$(command -v test-repository-receipt 2>/dev/null || true)
fi
if [ -z "$receipt_writer" ] || [ ! -x "$receipt_writer" ]; then
  printf 'FAIL: test-repository-receipt is required; build the qualified host tools first\n' >&2
  exit 2
fi
receipt_status=0
"$receipt_writer" \
  --results "$results_dir" \
  --output "$out_dir/receipt.json" || receipt_status=$?
if [ "$receipt_status" -ne 0 ] && [ "$failed" -eq 0 ]; then
  printf 'FAIL: repository-test receipt rejected an otherwise clean run\n' >&2
  failed=1
fi
[ "$failed" -eq 0 ]
