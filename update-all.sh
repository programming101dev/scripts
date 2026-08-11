#!/bin/sh
# update-all.sh — drive ./update.sh across the supported compiler lists
# Portable: POSIX sh (macOS, Linux, FreeBSD); uses grep, printf, getopts
#
# Pairing: each C compiler is paired with its matching C++ compiler by name
# (gcc-15 -> g++-15, clang -> clang++, clang22 -> clang++22). C compilers
# with no matching C++ compiler in the list are skipped with a warning —
# never paired positionally with an unrelated compiler.

set -eu

# Always operate from the directory this script lives in.
cd -- "$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck source=shared/compilers.sh
. ./shared/compilers.sh

# Defaults
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers=""
sanitizers_given=0
no_flags=0
standard=0
skip_install=0
interactive=0
latest=0
format=0
acceptance=1
acceptance_output=""
acceptance_no_cache=0
matrix_output=""

c_list_file="supported_c_compilers.txt"
cxx_list_file="supported_cxx_compilers.txt"
driver="./workspace/update.sh"

usage() {
    printf '%s\n' \
"Usage: $0 [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>] [-C <c-list>] [-X <cxx-list>] [-u <update.sh>]
  -f clang-format   Name of clang-format, default ${clang_format_name}
  -t clang-tidy     Name of clang-tidy,   default ${clang_tidy_name}
  -k cppcheck       Name of cppcheck,     default ${cppcheck_name}
  -s sanitizers     Comma list; default: current sanitizers.txt
  -C file           C compilers list,     default ${c_list_file}
  -X file           C++ compilers list,   default ${cxx_list_file}
  -u file           Path to update.sh,    default ${driver}
  -N                --no-flags: build every pair with NO probed compiler
                    flags and NO sanitizers (caches/selection untouched)
  -S                --standard: build every pair with the reasonable safe
                    subset (flag-selection.standard.json), no sanitizers
  --coverage        Opt-in: instrument every pair for code coverage (gcov)
  --profile         Opt-in: instrument every pair for profiling (gprof)
  --skip-install    Build every pair but do not run repo install.sh scripts
  --interactive     Pause, pull the pushed fix, and retry the failed phase
  --latest          Follow moving upstream branches instead of repos.lock;
                    refresh the lock before strict acceptance
  --format          Apply clang-format to every tracked workspace source
                    once, before the first pair builds, so the per-repo
                    format-check gate cannot fail on formatting alone.
                    Modifies tracked files.
  --skip-acceptance Build the compiler matrix but do not run the strict
                    CMake-owned host-tool qualification and workspace checks.
  --acceptance-output <dir>
                    Write the governed acceptance receipt and reports here.
                    Default: target/workspace/<host-pair>/acceptance.
  --acceptance-no-cache
                    Execute every governed acceptance node. This is intended
                    for release preflight; ordinary runs reuse exact receipts.
  --matrix-output <dir>
                    Store isolated compiler-pair logs and the parseable matrix
                    summary here. Default: target/update-all/<run-id>."
    if [ -f "$c_list_file" ]; then
        printf '\nCompiler pairs this will build (from %s):\n' "$c_list_file"
        while IFS= read -r _l || [ -n "$_l" ]; do
            _l=${_l%%#*}
            set -f; set -- $_l; set +f
            _c=${1:-}
            if [ -z "$_c" ]; then continue; fi
            _cb=$(basename "$_c")
            case "$_cb" in
              gcc*|clang*) _xb=$(p101_derive_cxx_name "$_cb") ;;
              *) _xb="(no C++ pair)" ;;
            esac
            printf '  %s : %s\n' "$_cb" "$_xb"
        done < "$c_list_file"
    fi
    exit 1
}

# --help / -h -> usage, exit 0 (P101 uniform CLI help)
case " $* " in *" --help "*|*" -h "*) ( usage ) || true; exit 0 ;; esac

# Parse short and long spellings without reconstructing argv through eval.
while [ "$#" -gt 0 ]; do
  case "$1" in
    -f|-t|-k|-s|-C|-X|-u)
      [ "$#" -ge 2 ] || { printf 'Error: %s requires an argument.\n' "$1" >&2; exit 2; }
      _opt=$1
      _value=$2
      case "$_opt" in
        -f) clang_format_name=$_value ;;
        -t) clang_tidy_name=$_value ;;
        -k) cppcheck_name=$_value ;;
        -s) sanitizers=$_value; sanitizers_given=1 ;;
        -C) c_list_file=$_value ;;
        -X) cxx_list_file=$_value ;;
        -u) driver=$_value ;;
      esac
      shift 2
      ;;
    -N|--no-flags) no_flags=1; shift ;;
    -S|--standard) standard=1; shift ;;
    -I|--skip-install) skip_install=1; shift ;;
    -i|--interactive) interactive=1; shift ;;
    --latest) latest=1; shift ;;
    --format) format=1; shift ;;
    --skip-acceptance) acceptance=0; shift ;;
    --acceptance-output)
      [ "$#" -ge 2 ] || { printf 'Error: --acceptance-output requires an argument.\n' >&2; exit 2; }
      acceptance_output=$2
      shift 2
      ;;
    --acceptance-no-cache) acceptance_no_cache=1; shift ;;
    --matrix-output)
      [ "$#" -ge 2 ] || { printf 'Error: --matrix-output requires an argument.\n' >&2; exit 2; }
      matrix_output=$2
      shift 2
      ;;
    --coverage) export P101_COVERAGE=1; shift ;;
    --profile) export P101_PROFILE=1; shift ;;
    -h|--help) usage ;;
    --) shift; [ "$#" -eq 0 ] || { printf 'Error: unexpected arguments.\n' >&2; exit 2; } ;;
    *)
      printf 'Error: unknown option: %s\n' "$1" >&2
      usage
      ;;
  esac
done

if [ "$no_flags" -eq 1 ] && [ "$standard" -eq 1 ]; then
  printf 'Error: -N/--no-flags and -S/--standard are mutually exclusive.\n' >&2
  exit 2
fi

# Preconditions
[ -f "$c_list_file" ]   || { printf 'Error: C list not found: %s\n' "$c_list_file" >&2; exit 2; }
[ -f "$cxx_list_file" ] || { printf 'Error: C++ list not found: %s\n' "$cxx_list_file" >&2; exit 2; }

abs_path() {
  # $1 = file path, absolute or relative to this scripts directory
  case "$1" in
    /*) printf '%s' "$1" ;;
    *)
      _dir=$(dirname "$1")
      _base=$(basename "$1")
      printf '%s/%s' "$(CDPATH='' cd -- "$_dir" && pwd -P)" "$_base"
      ;;
  esac
}

parallel_jobs() {
  _jobs=${CMAKE_BUILD_PARALLEL_LEVEL:-}
  case "$_jobs" in
    ''|*[!0-9]*|0)
      _jobs=$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)
      ;;
  esac
  case "$_jobs" in
    ''|*[!0-9]*|0)
      _jobs=$(sysctl -n hw.ncpu 2>/dev/null || true)
      ;;
  esac
  case "$_jobs" in
    ''|*[!0-9]*|0) _jobs=2 ;;
  esac
  printf '%s\n' "$_jobs"
}

# If update.sh decides the flag cache needs probing, pass the same compiler
# lists this update-all invocation is using. Without this, a CI smoke that
# asks for clang:clang++ still probes every detected compiler on a fresh runner
# before it builds anything.
flag_c_list_file=$(abs_path "$c_list_file")
flag_cxx_list_file=$(abs_path "$cxx_list_file")

# Resolve driver (name or path)
case "$driver" in
  /*|./*|../*)
    [ -x "$driver" ] || { printf 'Error: driver not executable: %s\n' "$driver" >&2; exit 2; }
    ;;
  *)
    driver_path=$(command -v "$driver" 2>/dev/null || true)
    [ -n "$driver_path" ] && driver=$driver_path
    [ -x "$driver" ] || { printf 'Error: driver not found/executable: %s\n' "$driver" >&2; exit 2; }
    ;;
esac

# Pull the scripts repo ONCE up front so a self-update aborts cleanly here,
# not partway through the compiler loop. A CI VM source snapshot is also a
# valid build input even though it cannot self-update.
pull_rc=0
./distribution/refresh-repo.sh --allow-snapshot . || pull_rc=$?
if [ "$pull_rc" -eq 1 ]; then
  printf 'The scripts repository was just updated. Please re-run: %s\n' "$0" >&2
  exit 1
elif [ "$pull_rc" -ne 0 ]; then
  printf 'Error: refresh-repo.sh failed (exit %d).\n' "$pull_rc" >&2
  exit "$pull_rc"
fi

# Derive the C++ compiler NAME that corresponds to a C compiler name.
derive_cxx() {
  p101_derive_cxx_name "$1"
}

# Find the list entry whose BASENAME matches $1. Supported lists hold names
# now; older generated lists may hold paths, so this still handles both.
find_by_basename() {
  p101_find_compiler_by_basename "$1" "$2"
}

skipped=0

# CR for CRLF-stripping without $'\r' (not POSIX sh)
cr=$(printf '\r')

# Build a stable manifest before changing the workspace. The manifest order is
# also the reporting order, independent of which concurrent worker finishes
# first.
run_stamp=$(date +%Y%m%dT%H%M%S)
if [ -z "$matrix_output" ]; then
  matrix_output="target/update-all/${run_stamp}-$$"
fi
case "$matrix_output" in
  /*) ;;
  *) matrix_output="$(pwd -P)/$matrix_output" ;;
esac
mkdir -p "$matrix_output"
rm -f -- "$matrix_output"/*.log "$matrix_output"/*.status \
  "$matrix_output"/*.elapsed "$matrix_output/pairs.tsv" \
  "$matrix_output/pids.txt" "$matrix_output/summary.tsv" \
  "$matrix_output/summary.md"
matrix_manifest="$matrix_output/pairs.tsv"
: > "$matrix_manifest"
pairs_run=0

while read -r c <&3 || [ -n "$c" ]; do
  c=${c%"$cr"}
  # Word-split to trim ALL surrounding whitespace (a trailing "spaces + CR"
  # sequence survives read's IFS stripping); compiler names contain no
  # spaces, so taking the first field is safe. set -f guards against glob
  # expansion of stray wildcards.
  set -f
  set -- $c
  set +f
  c=${1:-}
  # skip blanks and comments
  case "$c" in ''|\#*) continue ;; esac

  # entries are names or legacy full paths; pair by basename
  cbase=$(basename "$c")
  case "$cbase" in
    *[!A-Za-z0-9_.+-]*)
      printf 'WARN: compiler name cannot form an isolated build key: %s; skipping.\n' "$cbase" >&2
      skipped=$((skipped+1))
      continue
      ;;
  esac
  xbase=$(derive_cxx "$cbase")
  if [ -z "$xbase" ]; then
    printf 'WARN: no C++ counterpart rule for %s; skipping.\n' "$cbase" >&2
    skipped=$((skipped+1))
    continue
  fi

  x=$(find_by_basename "$xbase" "$cxx_list_file")
  if [ -z "$x" ]; then
    printf 'WARN: %s has no matching %s in %s; skipping.\n' "$cbase" "$xbase" "$cxx_list_file" >&2
    skipped=$((skipped+1))
    continue
  fi
  case "$xbase" in
    *[!A-Za-z0-9_.+-]*)
      printf 'WARN: C++ compiler name cannot form an isolated build key: %s; skipping.\n' "$xbase" >&2
      skipped=$((skipped+1))
      continue
      ;;
  esac

  pairs_run=$((pairs_run+1))
  pair_id=$(printf '%04d' "$pairs_run")
  pair_label=$(printf '%s__%s' "$cbase" "$xbase" | tr -c '[:alnum:]_.-' '_')
  printf '%s|%s|%s|%s\n' "$pair_id" "$c" "$x" "$pair_label" >> "$matrix_manifest"
done 3< "$c_list_file"

if [ "$pairs_run" -eq 0 ]; then
  printf 'Error: no usable C/C++ compiler pairs found (skipped: %d).\n' "$skipped" >&2
  exit 3
fi

IFS='|' read -r _host_id host_c host_x host_label < "$matrix_manifest"

run_driver() {
  _run_c=$1
  _run_x=$2
  _run_phase=$3
  _run_role=${4:-}
  set -- "$driver" -c "$_run_c" -x "$_run_x" \
    -C "$flag_c_list_file" -X "$flag_cxx_list_file" \
    -f "$clang_format_name" -t "$clang_tidy_name" -k "$cppcheck_name" \
    --skip-self-update
  case "$_run_phase" in
    prepare)
      set -- "$@" --prepare-only
      [ "$interactive" -eq 0 ] || set -- "$@" --interactive
      [ "$latest" -eq 0 ] || set -- "$@" --latest
      [ "$format" -eq 0 ] || set -- "$@" --format
      ;;
    build)
      set -- "$@" --build-only
      if [ "$_run_role" = host ] && [ "$skip_install" -eq 0 ]; then
        set -- "$@" --defer-install
      else
        set -- "$@" --skip-install
      fi
      ;;
    retry)
      set -- "$@" --build-only --interactive
      if [ "$_run_role" = host ] && [ "$skip_install" -eq 0 ]; then
        set -- "$@" --defer-install
      else
        set -- "$@" --skip-install
      fi
      ;;
    finalize)
      set -- "$@" --finalize-only
      [ "$skip_install" -eq 0 ] || set -- "$@" --skip-install
      ;;
    *)
      printf 'Error: internal matrix phase is invalid: %s\n' "$_run_phase" >&2
      return 2
      ;;
  esac
  if [ "$no_flags" -eq 1 ]; then
    set -- "$@" --no-flags
  elif [ "$standard" -eq 1 ]; then
    set -- "$@" --standard
  elif [ "$sanitizers_given" -eq 1 ]; then
    set -- "$@" -s "$sanitizers"
  fi
  "$@"
}

printf 'Compiler matrix output: %s\n' "$matrix_output"
printf 'Preparing shared workspace with host pair: %s : %s [%s]\n' \
  "$host_c" "$host_x" "$host_label"
prepare_log="$matrix_output/0000-prepare.log"
prepare_status=0
run_driver "$host_c" "$host_x" prepare > "$prepare_log" 2>&1 || prepare_status=$?
if [ "$prepare_status" -ne 0 ]; then
  printf 'update-all:prepare: error: workspace preparation failed with exit %d; log: %s [matrix-prepare-failure]\n' \
    "$prepare_status" "$prepare_log" >&2
  printf '%s\n' '--- workspace preparation log ---' >&2
  cat "$prepare_log" >&2
  printf '%s\n' '--- end workspace preparation log ---' >&2
  exit "$prepare_status"
fi
printf '[PASS] workspace preparation (log: %s)\n' "$prepare_log"

matrix_jobs=$(parallel_jobs)
pair_jobs=$((matrix_jobs / pairs_run))
[ "$pair_jobs" -gt 0 ] || pair_jobs=1
printf 'Starting %d compiler pair(s) in parallel (%d build job(s) per pair; %d total available).\n' \
  "$pairs_run" "$pair_jobs" "$matrix_jobs"
pids_file="$matrix_output/pids.txt"
: > "$pids_file"
terminate_matrix() {
  terminate_status=$1
  while IFS= read -r terminate_pid || [ -n "$terminate_pid" ]; do
    kill "$terminate_pid" 2>/dev/null || true
  done < "$pids_file"
  while IFS= read -r terminate_pid || [ -n "$terminate_pid" ]; do
    wait "$terminate_pid" 2>/dev/null || true
  done < "$pids_file"
  printf 'update-all:matrix: error: compiler matrix interrupted; logs: %s [compiler-matrix-interrupted]\n' \
    "$matrix_output" >&2
  exit "$terminate_status"
}
trap 'terminate_matrix 130' INT
trap 'terminate_matrix 143' TERM
while IFS='|' read -r pair_id c x pair_label; do
  pair_log="$matrix_output/${pair_id}-${pair_label}.log"
  pair_status="$matrix_output/${pair_id}.status"
  pair_elapsed="$matrix_output/${pair_id}.elapsed"
  pair_role=worker
  [ "$pair_id" != "$_host_id" ] || pair_role=host
  printf '[START] %s : %s (log: %s)\n' "$c" "$x" "$pair_log"
  (
    pair_start=$(date +%s)
    status=0
    CMAKE_BUILD_PARALLEL_LEVEL="$pair_jobs" \
      run_driver "$c" "$x" build "$pair_role" > "$pair_log" 2>&1 || status=$?
    pair_end=$(date +%s)
    elapsed=$((pair_end - pair_start))
    # Exit 125 is the matrix worker's explicit infrastructure-failure status.
    # Leave no receipt so the parent exercises the same recovery path as a
    # worker that disappeared before it could commit its status.  This is
    # deterministic across shells; killing a guessed parent process is not.
    [ "$status" -ne 125 ] || exit 125
    printf '%s\n' "$status" > "$pair_status"
    printf '%s\n' "$elapsed" > "$pair_elapsed"
    if [ "$status" -eq 0 ]; then
      printf '[PASS] %s : %s (%ss)\n' "$c" "$x" "$elapsed"
    else
      printf '[FAIL] %s : %s (exit %s, %ss; log: %s)\n' \
        "$c" "$x" "$status" "$elapsed" "$pair_log" >&2
    fi
  ) &
  printf '%s\n' "$!" >> "$pids_file"
done < "$matrix_manifest"

while IFS= read -r pair_pid || [ -n "$pair_pid" ]; do
  wait "$pair_pid" || true
done < "$pids_file"
trap - INT TERM

# A worker normally writes its own status receipt even when its build fails.
# If it was killed or otherwise disappeared before doing so, synthesize an
# explicit infrastructure failure instead of dying later on a missing `cat`.
while IFS='|' read -r pair_id c x pair_label; do
  pair_status="$matrix_output/${pair_id}.status"
  pair_elapsed="$matrix_output/${pair_id}.elapsed"
  pair_log="$matrix_output/${pair_id}-${pair_label}.log"
  status_value=""
  elapsed_value=""
  [ ! -f "$pair_status" ] || status_value=$(cat "$pair_status")
  [ ! -f "$pair_elapsed" ] || elapsed_value=$(cat "$pair_elapsed")
  case "$status_value" in
    ''|*[!0-9]*)
      printf '%s\n' 125 > "$pair_status"
      printf 'update-all:%s: error: compiler worker ended without a valid status receipt [compiler-worker-receipt-missing]\n' \
        "$pair_label" >> "$pair_log"
      ;;
  esac
  case "$elapsed_value" in
    ''|*[!0-9]*) printf '%s\n' 0 > "$pair_elapsed" ;;
  esac
done < "$matrix_manifest"

matrix_failures=0
while IFS='|' read -r pair_id c x pair_label; do
  status=$(cat "$matrix_output/${pair_id}.status")
  if [ "$status" -ne 0 ]; then
    matrix_failures=$((matrix_failures + 1))
    pair_log="$matrix_output/${pair_id}-${pair_label}.log"
    printf 'update-all:%s: error: compiler pair %s : %s failed with exit %s; log: %s [compiler-pair-failure]\n' \
      "$pair_label" "$c" "$x" "$status" "$pair_log" >&2
    printf '%s\n' "--- failure log: $c : $x ---" >&2
    cat "$pair_log" >&2
    printf '%s\n' "--- end failure log: $c : $x ---" >&2
  fi
done < "$matrix_manifest"

# Parallel workers never compete for stdin. Interactive recovery begins only
# after every worker has stopped, and retries failed pairs in manifest order.
if [ "$matrix_failures" -gt 0 ] && [ "$interactive" -eq 1 ]; then
  while IFS='|' read -r pair_id c x pair_label; do
    status=$(cat "$matrix_output/${pair_id}.status")
    [ "$status" -ne 0 ] || continue
    pair_role=worker
    [ "$pair_id" != "$_host_id" ] || pair_role=host
    printf '\nRetrying failed compiler pair interactively: %s : %s\n' "$c" "$x" >&2
    retry_start=$(date +%s)
    retry_log="$matrix_output/${pair_id}-${pair_label}.retry.log"
    retry_status_file="$matrix_output/${pair_id}.retry.status"
    rm -f -- "$retry_log" "$retry_status_file"
    retry_tee_status=0
    (
      retry_worker_status=0
      CMAKE_BUILD_PARALLEL_LEVEL="$matrix_jobs" \
        run_driver "$c" "$x" retry "$pair_role" || retry_worker_status=$?
      printf '%s\n' "$retry_worker_status" > "$retry_status_file"
      exit "$retry_worker_status"
    ) 2>&1 | tee "$retry_log" || retry_tee_status=$?
    retry_status=125
    if [ "$retry_tee_status" -eq 0 ] && [ -f "$retry_status_file" ]; then
      retry_status=$(cat "$retry_status_file")
      case "$retry_status" in
        ''|*[!0-9]*) retry_status=125 ;;
      esac
    fi
    if [ "$retry_status" -eq 125 ]; then
      printf 'update-all:%s: error: interactive retry ended without a valid status receipt [compiler-retry-receipt-missing]\n' \
        "$pair_label" | tee -a "$retry_log" >&2
    fi
    retry_end=$(date +%s)
    printf '%s\n' "$retry_status" > "$matrix_output/${pair_id}.status"
    printf '%s\n' "$((retry_end - retry_start))" > "$matrix_output/${pair_id}.elapsed"
  done < "$matrix_manifest"
fi

summary_tsv="$matrix_output/summary.tsv"
summary_md="$matrix_output/summary.md"
printf 'index\tc_compiler\tcxx_compiler\tstatus\texit\telapsed_seconds\tlog\n' > "$summary_tsv"
printf '# p101 compiler matrix\n\n| C compiler | C++ compiler | Result | Exit | Seconds | Log |\n| --- | --- | --- | ---: | ---: | --- |\n' > "$summary_md"
matrix_failures=0
while IFS='|' read -r pair_id c x pair_label; do
  status=$(cat "$matrix_output/${pair_id}.status")
  elapsed=$(cat "$matrix_output/${pair_id}.elapsed")
  pair_log="$matrix_output/${pair_id}-${pair_label}.log"
  retry_log="$matrix_output/${pair_id}-${pair_label}.retry.log"
  [ ! -f "$retry_log" ] || pair_log="$retry_log"
  result=PASS
  if [ "$status" -ne 0 ]; then
    result=FAIL
    matrix_failures=$((matrix_failures + 1))
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$pair_id" "$c" "$x" "$result" "$status" "$elapsed" "$pair_log" >> "$summary_tsv"
  printf '| %s | %s | %s | %s | %s | `%s` |\n' \
    "$c" "$x" "$result" "$status" "$elapsed" "$pair_log" >> "$summary_md"
done < "$matrix_manifest"

if [ "$matrix_failures" -gt 0 ]; then
  printf 'Compiler matrix failed: %d of %d pair(s). Summary: %s\n' \
    "$matrix_failures" "$pairs_run" "$summary_tsv" >&2
  exit 1
fi

printf 'Finalizing host artifacts: %s : %s\n' "$host_c" "$host_x"
finalize_log="$matrix_output/9999-finalize.log"
finalize_status=0
run_driver "$host_c" "$host_x" finalize > "$finalize_log" 2>&1 || finalize_status=$?
if [ "$finalize_status" -ne 0 ]; then
  printf 'update-all:finalize: error: host artifact finalization failed with exit %d; log: %s [matrix-finalize-failure]\n' \
    "$finalize_status" "$finalize_log" >&2
  cat "$finalize_log" >&2
  exit "$finalize_status"
fi

printf 'Done: %d compiler pair(s) built in parallel, %d skipped.\n' "$pairs_run" "$skipped"
printf 'Matrix summary: %s\n' "$summary_tsv"

if [ "$acceptance" -eq 1 ]; then
  host_cc=$(p101_resolve_compiler "$host_c" compiler_paths.txt)
  host_cxx=$(p101_resolve_compiler "$host_x" compiler_paths.txt)
  host_name=$(printf '%s__%s' "$(basename "$host_c")" "$(basename "$host_x")" | tr -c '[:alnum:]_.-' '_')
  host_build="target/workspace/$host_name"
  if [ -z "$acceptance_output" ]; then
    acceptance_output="$host_build/acceptance"
  else
    case "$acceptance_output" in
      /*) ;;
      *) acceptance_output="$(pwd -P)/$acceptance_output" ;;
    esac
  fi
  printf 'Configuring CMake-owned host tools: %s\n' "$host_build"
  cmake -S workspace -B "$host_build" \
    -DCMAKE_C_COMPILER="$host_cc" \
    -DP101_ACCEPTANCE_CXX_COMPILER="$host_cxx" \
    -DP101_ACCEPTANCE_OUTPUT_DIR="$acceptance_output" \
    -DP101_ACCEPTANCE_NO_CACHE="$acceptance_no_cache"
  printf 'Running strict CMake acceptance target.\n'
  acceptance_jobs=$(parallel_jobs)
  P101_QUIET=1 cmake --build "$host_build" --target p101_acceptance --parallel "$acceptance_jobs"
fi
