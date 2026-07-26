#!/usr/bin/env bash
# check-templates-standalone.sh — prove copied templates are self-contained.
#
# The template repos may live inside the broader p101 workspace, and the copied
# projects may intentionally symlink expensive shared artifacts such as .flags.
# The scripts inside the copied projects, however, must stand on their own: no
# implicit ../scripts lookup, no dependence on the caller's current directory,
# and materialized CMake helper files.

set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

cc="clang"
cxx="clang++"
out_dir=""
keep=0
run_build=1
run_tests=1

usage() {
  cat <<'USAGE'
Usage: ./check-templates-standalone.sh [options]

Copies each template to a temporary directory from outside the template repo,
then verifies that the copied project is self-contained except for intentional
shared-artifact symlinks.

Options:
  -c <cc>       C compiler used for copied C templates. Default: clang.
  -x <cxx>      C++ compiler used for copied C++ template. Default: clang++.
  -o <dir>      Output directory. Default: fresh /tmp directory.
  -k            Keep the output directory even on success.
  --no-build    Copy and audit only; skip configure/build/test.
  --no-tests    Configure/build, but skip copied template tests.
  -h, --help    Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    -c) cc="${2:?}"; shift 2 ;;
    -x) cxx="${2:?}"; shift 2 ;;
    -o) out_dir="${2:?}"; shift 2 ;;
    -k) keep=1; shift ;;
    --no-build) run_build=0; run_tests=0; shift ;;
    --no-tests) run_tests=0; shift ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -z "$out_dir" ]; then
  out_dir="${TMPDIR:-/tmp}/p101-template-standalone-$$"
fi

out_dir="$(mkdir -p "$out_dir" && cd "$out_dir" && pwd)"
log_dir="$out_dir/logs"
mkdir -p "$log_dir"

failed=0

cleanup() {
  if [ "$failed" -eq 0 ] && [ "$keep" -eq 0 ]; then
    rm -rf "$out_dir"
  fi
}
trap cleanup EXIT

say() {
  printf '%s\n' "$*"
}

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  failed=1
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
    fail "$title; see $log"
  fi
}

require_file() {
  path="$1"
  [ -f "$path" ] || fail "missing required file: $path"
}

require_executable() {
  path="$1"
  [ -x "$path" ] || fail "missing required executable: $path"
}

require_cmake_helpers() {
  dir="$1"
  require_file "$dir/cmake/FailIfCppcheckDiagnostics.cmake"
  require_file "$dir/cmake/RunClangTidyOverList.cmake"
  if [ -L "$dir/cmake" ]; then
    fail "$dir/cmake must be materialized in copied templates, not a symlink"
  fi
}

check_allowed_symlinks() {
  dir="$1"
  template="$2"

  while IFS= read -r link_path; do
    name="$(basename "$link_path")"
    case "$name" in
      .flags|sanitizers.txt|supported_c_compilers.txt|supported_cxx_compilers.txt)
        ;;
      *)
        fail "$template copied unexpected top-level symlink: $name -> $(readlink "$link_path")"
        ;;
    esac
  done < <(find "$dir" -maxdepth 1 -type l -print)
}

check_script_references() {
  dir="$1"
  template="$2"
  bad_refs="$log_dir/${template}-bad-script-refs.txt"

  # Scripts may use paths within the copied project, but they must not search
  # parent workspace layouts or bake in this developer's absolute workspace.
  if find "$dir" -name '*.sh' -type f -exec grep -nE '(\.\./scripts|\.\./\.\./scripts|/Users/ds|programming101dev|source_dir/\.\./\.flags)' {} + > "$bad_refs" 2>/dev/null; then
    fail "$template copied scripts contain non-standalone references; see $bad_refs"
  else
    rm -f "$bad_refs"
  fi
}

check_copy_shape() {
  dir="$1"
  template="$2"

  require_executable "$dir/change-compiler.sh"
  require_executable "$dir/build.sh"
  require_executable "$dir/test.sh"
  require_executable "$dir/doctor.sh"
  require_executable "$dir/test-all.sh"
  require_file "$dir/CMakeLists.txt"
  require_file "$dir/config.cmake"
  require_cmake_helpers "$dir"
  check_allowed_symlinks "$dir" "$template"
  check_script_references "$dir" "$template"
}

copy_and_check() {
  template="$1"
  src="$2"
  lang="$3"

  dest="$out_dir/$template"
  copy_log="$log_dir/${template}-copy.log"

  run_logged "copy $template" "$copy_log" "$src/copy-template.sh" -q "$dest"
  check_copy_shape "$dest" "$template"

  if [ "$run_build" -eq 1 ] && [ "$failed" -eq 0 ]; then
    build_log="$log_dir/${template}-build.log"

    if [ "$lang" = "cxx" ]; then
      run_logged "configure/build copied $template" "$build_log" \
        bash -c 'cd "$1" && ./change-compiler.sh -c "$2" -b build-standalone-check && ./build.sh -q' \
        sh "$dest" "$cxx"
    else
      run_logged "configure/build copied $template" "$build_log" \
        bash -c 'cd "$1" && ./change-compiler.sh -c "$2" -b build-standalone-check && ./build.sh -q' \
        sh "$dest" "$cc"
    fi
  fi

  if [ "$run_tests" -eq 1 ] && [ "$failed" -eq 0 ]; then
    test_log="$log_dir/${template}-test.log"
    run_logged "test copied $template" "$test_log" bash -c 'cd "$1" && ./test.sh' sh "$dest"
  fi
}

say "Template standalone check output: $out_dir"

copy_and_check "template-c" "../templates/template-c" "c"
copy_and_check "template-c-program" "../templates/template-c-program" "c"
copy_and_check "template-cxx" "../templates/template-cxx" "cxx"

if [ "$failed" -ne 0 ]; then
  say "Template standalone check failed; kept output: $out_dir"
  exit 1
fi

say "Template standalone check passed."
if [ "$keep" -eq 1 ]; then
  say "Kept output: $out_dir"
fi
