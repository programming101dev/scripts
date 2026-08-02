#!/usr/bin/env bash
# Run each repository-owned unit suite and a bounded fuzz smoke where supported.
set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

out_dir=""
fuzz_secs=5
skip_fuzz=0
c_compiler=""
cxx_compiler=""
jobs="${P101_JOBS:-0}"

usage() {
  cat <<'USAGE'
Usage: ./check-repository-tests.sh [-c <cc>] [-x <cxx>] [-o <dir>] [-j <jobs>] [--fuzz-secs <seconds>] [--skip-fuzz]

Runs every standalone test.sh named by repos.txt, plus local p101-* program
repositories that have not yet been added to that manifest. Repositories with a
fuzz target also receive a bounded fuzz run when a fuzzer-capable compiler is
available. A missing test suite is reported as NO TEST rather than silently
treated as tested.
When -c/-x is supplied, unit tests and fuzzing use those compilers. This keeps
test executables, fuzz executables, and sanitizer-instrumented p101 libraries
on the same runtime.

Repositories run concurrently with a conservative host-derived default capped
at two workers. Use -j 1 for serial execution. P101_JOBS supplies the default
when -j is omitted.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -c) c_compiler="${2:?}"; shift 2 ;;
    -x) cxx_compiler="${2:?}"; shift 2 ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    -j|--jobs) jobs="${2:?}"; shift 2 ;;
    --fuzz-secs) fuzz_secs="${2:?}"; shift 2 ;;
    --skip-fuzz) skip_fuzz=1; shift ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

resolve_compiler() {
  requested="$1"
  [ -n "$requested" ] || return 0
  if [ -f compiler_paths.txt ]; then
    resolved="$(awk -F= -v name="$requested" '$1 == name { print substr($0, index($0, "=") + 1); exit }' compiler_paths.txt)"
    if [ -n "$resolved" ]; then
      printf '%s\n' "$resolved"
      return 0
    fi
  fi
  command -v "$requested" 2>/dev/null || printf '%s\n' "$requested"
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
  if [ "$has_unit_suite" -eq 1 ] && [ -x "$repo/test.sh" ]; then
    if (CDPATH='' cd "$repo" && P101_TEST_CC="$c_compiler" P101_TEST_CXX="$cxx_compiler" ./test.sh) > "$test_log" 2>&1; then
      unit="PASS"
    else
      unit="FAIL"
    fi
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
    if (CDPATH='' cd "$repo" && "${fuzz_command[@]}" --can-fuzz) >/dev/null 2>&1; then
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
  cost=1
  if [ -f "$cost_contract" ]; then
    configured_cost="$(awk -F'|' -v name="$name" '$1 == name { print $2; exit }' "$cost_contract")"
    case "$configured_cost" in *[!0-9]*|'') ;; *) cost="$configured_cost" ;; esac
  fi
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
  run_repository "$repository_index" "$relative" "$language" &
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
  if [ "$unit" = "FAIL" ]; then
    failure_logs+=("$name unit tests|$test_log")
    failed=1
  elif [ "$unit" = "MISSING" ]; then
    failed=1
  fi
  if [ "$fuzz" = "FAIL" ]; then
    failure_logs+=("$name fuzz smoke|$fuzz_log")
    failed=1
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
printf 'Repository test summary: %s\n' "$summary"
[ "$failed" -eq 0 ]
