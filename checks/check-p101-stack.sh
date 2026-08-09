#!/usr/bin/env bash
# check-p101-stack.sh — acceptance suite for the p101 workspace.
#
# This is the "does the whole thing hang together?" ratchet:
#   1. prove every emitted diagnostic maps to a checked lesson;
#   2. strict-build wrapper libraries and tools/templates from repos.txt;
#   3. prove fresh template instances are self-contained;
#   4. run the tool playground tour, including observe/resource/trace/report and
#      error-path walking through audit-doctor.

set -euo pipefail
unset CDPATH
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
# shellcheck source=shared/compilers.sh
. ./shared/compilers.sh

cc="clang"
cxx="clang++"
clang_format="clang-format"
clang_tidy="clang-tidy"
cppcheck="cppcheck"
sanitizers=""
out_dir=""
skip_repo_build=0
skip_install=0
skip_templates=0
skip_playground=0
skip_corpus=0
skip_lab=0
template_no_tests=0
playground_skip_quality=1
playground_skip_coverage=1
playground_skip_fuzz=1
fault_count=1
fuzz_secs=5

usage() {
  cat <<'USAGE'
Usage: ./check-p101-stack.sh [options]

Runs the p101 acceptance stack: repo builds, standalone template
instantiation/build, and the p101-tool-playground tour including audit-doctor.

Options:
  -c <cc>          C compiler. Default: clang.
  -x <cxx>         C++ compiler. Default: clang++.
  -f <formatter>   clang-format executable. Default: clang-format.
  -t <tidy>        clang-tidy executable. Default: clang-tidy.
  -k <cppcheck>    cppcheck executable. Default: cppcheck.
  -s <list>        Sanitizers passed to build-repo.sh.
  -o <dir>         Artifact directory. Default: /tmp/p101-stack-check-<pid>.
  -n <count>       Fault-injection cases for the playground tour. Default: 1.
  --fuzz-secs <s>  Fuzz smoke budget if playground fuzz is enabled. Default: 5.

  --skip-repo-build     Do not run build-repo.sh.
  --skip-install        Build repos but do not run each repo's install.sh.
  --skip-templates      Do not run check-templates-standalone.sh.
  --skip-playground     Do not run p101-tool-playground/tour.sh.
  --skip-corpus         Do not run the p101-tool-playground corpus smoke.
  --skip-lab            Do not run the p101-tool-playground lab-book smoke.
  --template-no-tests   Build fresh template instances but skip their tests.

  --playground-quality  Let tour.sh run build/test/fuzz/coverage quality steps.
  --playground-coverage Let tour.sh run coverage.
  --playground-fuzz     Let tour.sh run fuzz smoke.
  -h, --help            Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -c) cc="${2:?}"; shift 2 ;;
    -x) cxx="${2:?}"; shift 2 ;;
    -f) clang_format="${2:?}"; shift 2 ;;
    -t) clang_tidy="${2:?}"; shift 2 ;;
    -k) cppcheck="${2:?}"; shift 2 ;;
    -s) sanitizers="${2:?}"; shift 2 ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    -n) fault_count="${2:?}"; shift 2 ;;
    --fuzz-secs) fuzz_secs="${2:?}"; shift 2 ;;
    --skip-repo-build) skip_repo_build=1; shift ;;
    --skip-install) skip_install=1; shift ;;
    --skip-templates) skip_templates=1; shift ;;
    --skip-playground) skip_playground=1; shift ;;
    --skip-corpus) skip_corpus=1; shift ;;
    --skip-lab) skip_lab=1; shift ;;
    --template-no-tests) template_no_tests=1; shift ;;
    --playground-quality) playground_skip_quality=0; shift ;;
    --playground-coverage) playground_skip_coverage=0; shift ;;
    --playground-fuzz) playground_skip_fuzz=0; shift ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

resolve_compiler() {
  p101_resolve_compiler "$1" compiler_paths.txt
}

cc="$(resolve_compiler "$cc")"
cxx="$(resolve_compiler "$cxx")"

if [ -z "$out_dir" ]; then
  out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-stack-check.XXXXXX")"
fi

out_dir="$(mkdir -p "$out_dir" && CDPATH='' cd -P "$out_dir" && pwd -P)"
log_dir="$out_dir/logs"
mkdir -p "$log_dir"

say() {
  printf '%s\n' "$*"
}

reset_child_dir() {
  child="$1"

  case "$child" in
    "$out_dir"/*)
      rm -rf "$child"
      ;;
    *)
      echo "Refusing to remove path outside output directory: $child" >&2
      exit 3
      ;;
  esac
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
  else
    rc=$?
    say "    FAIL (exit $rc; see $log)"
    say "    --- failure log ---"
    cat "$log" || true
    say "    --- end failure log ---"
    return "$rc"
  fi
}

say "p101 stack check output: $out_dir"

run_logged "finding-to-lesson curriculum completeness" "$log_dir/p101-lessons.log" ./runtime/p101_lessons.py check
run_logged "executable lesson acceptance (quick)" "$log_dir/p101-lessons-acceptance.log" \
  ./runtime/p101_lessons.py verify --quick -o "$out_dir/lesson-acceptance"

if [ "$skip_repo_build" -eq 0 ]; then
  build_args=(-c "$cc" -x "$cxx" -f "$clang_format" -t "$clang_tidy" -k "$cppcheck")
  if [ -n "$sanitizers" ]; then
    build_args+=(-s "$sanitizers")
  fi
  if [ "$skip_install" -eq 1 ]; then
    build_args+=(-I)
  fi
  run_logged "strict-build repos from repos.txt" "$log_dir/repos-build.log" ./workspace/build-repo.sh "${build_args[@]}" -S
else
  say "==> strict-build repos from repos.txt"
  say "    SKIP"
fi

if [ "$skip_templates" -eq 0 ]; then
  template_args=(-c "$cc" -x "$cxx" -o "$out_dir/templates" -k)
  if [ "$template_no_tests" -eq 1 ]; then
    template_args+=(--no-tests)
  fi
  run_logged "standalone fresh template instances" "$log_dir/templates.log" ./checks/check-templates-standalone.sh "${template_args[@]}"
else
  say "==> standalone fresh template instances"
  say "    SKIP"
fi

if [ "$skip_playground" -eq 0 ]; then
  playground_out="$out_dir/playground-tour"
  reset_child_dir "$playground_out"
  tour_args=(-o "$playground_out" -n "$fault_count" -t "$fuzz_secs" -c "$cc")
  if [ "$playground_skip_quality" -eq 1 ]; then
    tour_args+=(--skip-quality)
  fi
  if [ "$playground_skip_coverage" -eq 1 ]; then
    tour_args+=(--skip-coverage)
  fi
  if [ "$playground_skip_fuzz" -eq 1 ]; then
    tour_args+=(--skip-fuzz)
  fi
  run_logged "p101-tool-playground tour" "$log_dir/playground-tour.log" ../playgrounds/tour.sh "${tour_args[@]}"
else
  say "==> p101-tool-playground tour"
  say "    SKIP"
fi

if [ "$skip_corpus" -eq 0 ]; then
  corpus_out="$out_dir/p101-corpus"
  reset_child_dir "$corpus_out"
  run_logged "p101-tool-playground corpus smoke" "$log_dir/p101-corpus.log" ../playgrounds/corpus.sh --quick -o "$corpus_out"
else
  say "==> p101-tool-playground corpus smoke"
  say "    SKIP"
fi

if [ "$skip_lab" -eq 0 ]; then
  lab_out="$out_dir/p101-lab"
  reset_child_dir "$lab_out"
  run_logged "p101-tool-playground lab-book smoke" "$log_dir/p101-lab.log" ../playgrounds/lab.sh --quick --strict-corpus -o "$lab_out"
else
  say "==> p101-tool-playground lab-book smoke"
  say "    SKIP"
fi

say "p101 stack check passed: $out_dir"
