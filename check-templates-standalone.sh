#!/usr/bin/env bash
# check-templates-standalone.sh — prove fresh template instances are self-contained.
#
# The template repos may live inside the broader p101 workspace. A fresh
# instance may intentionally symlink expensive shared artifacts such as .flags,
# but its scripts must stand on their own: no implicit ../scripts lookup, no
# dependence on the caller's current directory, and materialized CMake helper
# files.

set -euo pipefail
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

cc="clang"
cxx="clang++"
out_dir=""
automatic_out_dir=0
keep=0
run_build=1
run_tests=1

usage() {
  cat <<'USAGE'
Usage: ./check-templates-standalone.sh [options]

Instantiates each template in a temporary directory from outside the template
repo, then verifies that the fresh project instance is self-contained except
for intentional shared-artifact symlinks.

Options:
  -c <cc>       C compiler used for fresh C template instances. Default: clang.
  -x <cxx>      C++ compiler used for fresh C++ template instances. Default: clang++.
  -o <dir>      Output directory. Default: fresh /tmp directory.
  -k            Keep the output directory even on success.
  --no-build    Copy and audit only; skip configure/build/test.
  --no-tests    Configure/build, but skip fresh instance tests.
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
  out_dir="$(mktemp -d "${TMPDIR:-/tmp}/p101-template-standalone.XXXXXX")"
  automatic_out_dir=1
fi

out_dir="$(mkdir -p "$out_dir" && CDPATH='' cd -P "$out_dir" && pwd -P)"
log_dir="$out_dir/logs"
mkdir -p "$log_dir"

failed=0

cleanup() {
  if [ "$automatic_out_dir" -eq 1 ] && [ "$failed" -eq 0 ] && [ "$keep" -eq 0 ]; then
    rm -rf "$out_dir"
  fi
}
trap cleanup EXIT

say() {
  printf '%s\n' "$*"
}

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  failed=$((failed + 1))
}

show_log_tail() {
  local log="$1"

  if [ -f "$log" ]; then
    printf '    --- log tail: %s ---\n' "$log" >&2
    tail -n 120 "$log" >&2 || true
    printf '    --- end log tail ---\n' >&2
  fi
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
    show_log_tail "$log"
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
    fail "$dir/cmake must be materialized in fresh template instances, not a symlink"
  fi
}

reset_destination() {
  dest="$1"

  case "$dest" in
    "$out_dir"/*)
      rm -rf "$dest"
      ;;
    *)
      fail "refusing to remove destination outside output directory: $dest"
      ;;
  esac
}

check_allowed_symlinks() {
  dir="$1"
  template="$2"

  while IFS= read -r link_path; do
    name="$(basename "$link_path")"
    case "$name" in
      .flags|sanitizers.txt|supported_c_compilers.txt|supported_cxx_compilers.txt)
        if [ ! -e "$link_path" ]; then
          fail "$template fresh instance has dangling symlink: $name -> $(readlink "$link_path")"
        fi
        ;;
      *)
        fail "$template fresh instance has unexpected top-level symlink: $name -> $(readlink "$link_path")"
        ;;
    esac
  done < <(find "$dir" -maxdepth 1 -type l -print)
}

check_script_references() {
  dir="$1"
  template="$2"
  bad_refs="$log_dir/${template}-bad-script-refs.txt"

  # Scripts may use paths within the fresh project instance, but they must not search
  # parent workspace layouts or bake in this developer's absolute workspace.
  if find "$dir" -name '*.sh' -type f -exec grep -nE '(\.\./scripts|\.\./\.\./scripts|/Users/ds|programming101dev|source_dir/\.\./\.flags)' {} + > "$bad_refs" 2>/dev/null; then
    fail "$template fresh-instance scripts contain non-standalone references; see $bad_refs"
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
  require_file "$dir/sanitizers.txt"
  require_cmake_helpers "$dir"
  if [ "$template" = "template-cxx" ]; then
    require_file "$dir/.flags/$(basename "$cxx")/warning_flags.txt"
  else
    require_file "$dir/.flags/$(basename "$cc")/warning_flags.txt"
  fi
  check_allowed_symlinks "$dir" "$template"
  check_script_references "$dir" "$template"
}

copy_and_check() {
  template="$1"
  src="$2"
  lang="$3"

  dest="$out_dir/$template"
  copy_log="$log_dir/${template}-copy.log"
  template_failures="$failed"

  reset_destination "$dest"
  run_logged "copy $template" "$copy_log" "$src/copy-template.sh" -q "$dest"
  check_copy_shape "$dest" "$template"

  if [ "$run_build" -eq 1 ] && [ "$failed" -eq "$template_failures" ]; then
    build_log="$log_dir/${template}-build.log"

    if [ "$lang" = "cxx" ]; then
      run_logged "configure/build fresh $template instance" "$build_log" \
        bash -c 'cd "$1" && ./change-compiler.sh -c "$2" -b build-standalone-check && ./build.sh -q' \
        sh "$dest" "$cxx"
    else
      run_logged "configure/build fresh $template instance" "$build_log" \
        bash -c 'cd "$1" && ./change-compiler.sh -c "$2" -b build-standalone-check && ./build.sh -q' \
        sh "$dest" "$cc"
    fi
  fi

  if [ "$run_tests" -eq 1 ] && [ "$failed" -eq "$template_failures" ]; then
    test_log="$log_dir/${template}-test.log"
    run_logged "test fresh $template instance" "$test_log" bash -c 'cd "$1" && ./test.sh' sh "$dest"
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
