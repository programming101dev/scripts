#!/usr/bin/env bash
# check-p101-stack.sh — acceptance suite for the p101 workspace.
#
# This is the "does the whole thing hang together?" ratchet:
#   1. strict-build wrapper libraries and tools/templates from repos.txt;
#   2. prove copied templates are self-contained;
#   3. run the tool playground tour, including observe/resource/trace/report and
#      error-path walking through p101-audit.

set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

cc="clang"
cxx="clang++"
clang_format="clang-format"
clang_tidy="clang-tidy"
cppcheck="cppcheck"
sanitizers=""
out_dir=""
skip_repo_build=0
skip_templates=0
skip_playground=0
template_no_tests=0
playground_skip_quality=1
playground_skip_coverage=1
playground_skip_fuzz=1
fault_count=1
fuzz_secs=5

usage() {
  cat <<'USAGE'
Usage: ./check-p101-stack.sh [options]

Runs the p101 acceptance stack: repo builds, standalone template copy/build,
and the p101-tool-playground tour including p101-audit.

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
  --skip-templates      Do not run check-templates-standalone.sh.
  --skip-playground     Do not run p101-tool-playground/tour.sh.
  --template-no-tests   Build copied templates but skip copied template tests.

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
    --skip-templates) skip_templates=1; shift ;;
    --skip-playground) skip_playground=1; shift ;;
    --template-no-tests) template_no_tests=1; shift ;;
    --playground-quality) playground_skip_quality=0; shift ;;
    --playground-coverage) playground_skip_coverage=0; shift ;;
    --playground-fuzz) playground_skip_fuzz=0; shift ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$out_dir" ]; then
  out_dir="${TMPDIR:-/tmp}/p101-stack-check-$$"
fi

out_dir="$(mkdir -p "$out_dir" && cd "$out_dir" && pwd)"
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

  "$@" >> "$log" 2>&1
  say "    PASS"
}

say "p101 stack check output: $out_dir"

if [ "$skip_repo_build" -eq 0 ]; then
  build_args=(-c "$cc" -x "$cxx" -f "$clang_format" -t "$clang_tidy" -k "$cppcheck")
  if [ -n "$sanitizers" ]; then
    build_args+=(-s "$sanitizers")
  fi
  run_logged "strict-build repos from repos.txt" "$log_dir/repos-build.log" ./build-repo.sh "${build_args[@]}" -S
else
  say "==> strict-build repos from repos.txt"
  say "    SKIP"
fi

if [ "$skip_templates" -eq 0 ]; then
  template_args=(-c "$cc" -x "$cxx" -o "$out_dir/templates" -k)
  if [ "$template_no_tests" -eq 1 ]; then
    template_args+=(--no-tests)
  fi
  run_logged "standalone copied templates" "$log_dir/templates.log" ./check-templates-standalone.sh "${template_args[@]}"
else
  say "==> standalone copied templates"
  say "    SKIP"
fi

if [ "$skip_playground" -eq 0 ]; then
  playground_out="$out_dir/playground-tour"
  reset_child_dir "$playground_out"
  tour_args=(-o "$playground_out" -n "$fault_count" -t "$fuzz_secs")
  if [ "$playground_skip_quality" -eq 1 ]; then
    tour_args+=(--skip-quality)
  fi
  if [ "$playground_skip_coverage" -eq 1 ]; then
    tour_args+=(--skip-coverage)
  fi
  if [ "$playground_skip_fuzz" -eq 1 ]; then
    tour_args+=(--skip-fuzz)
  fi
  run_logged "p101-tool-playground tour" "$log_dir/playground-tour.log" ../programs/p101-tool-playground/tour.sh "${tour_args[@]}"
else
  say "==> p101-tool-playground tour"
  say "    SKIP"
fi

say "p101 stack check passed: $out_dir"
