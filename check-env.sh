#!/usr/bin/env bash
set -euo pipefail

# Always operate from the directory this script lives in, so outputs like
# sanitizers.txt land in the scripts repo no matter where we're invoked from.
CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

c_compiler=""
cxx_compiler=""
clang_format_name="clang-format"
clang_tidy_name="clang-tidy"
cppcheck_name="cppcheck"
sanitizers=""
sanitizers_given=false

usage() {
  cat <<'USAGE'
Usage: check-env.sh [-c <C compiler>] [-x <C++ compiler>] [-f <clang-format>] [-t <clang-tidy>] [-k <cppcheck>] [-s <sanitizers>] [-h]
  -c <cc>          C compiler (e.g. gcc, clang, gcc-15); optional — when
                   omitted, only the generic tools are checked
  -x <cxx>         C++ compiler (e.g. g++, clang++, g++-15); optional
  -f <name>        clang-format executable name [default: clang-format]
  -t <name>        clang-tidy executable name   [default: clang-tidy]
  -k <name>        cppcheck executable name     [default: cppcheck]
  -s <list>        sanitizers (comma-separated, optional; e.g. address,undefined)
  -h               show this help and exit
Exit status: number of missing/invalid tools (0 means all good).
             64 indicates a usage error (bad/missing arguments).
USAGE
}

# --help / -h -> usage, exit 0 (P101 uniform CLI help)
case " $* " in *" --help "*|*" -h "*) ( usage ) || true; exit 0 ;; esac

# Parse options
while getopts ":c:x:f:t:k:s:h" opt; do
  case "$opt" in
    c) c_compiler="$OPTARG" ;;
    x) cxx_compiler="$OPTARG" ;;
    f) clang_format_name="$OPTARG" ;;
    t) clang_tidy_name="$OPTARG" ;;
    k) cppcheck_name="$OPTARG" ;;
    s) sanitizers="$OPTARG"; sanitizers_given=true ;;
    h) usage; exit 0 ;;
    \?) echo "Invalid option: -$OPTARG" >&2; usage; exit 64 ;;
    :)  echo "Option -$OPTARG requires an argument." >&2; usage; exit 64 ;;
  esac
done

# -c and -x are optional: a bare ./check-env.sh (as the README suggests)
# checks the generic tools; compilers are checked only when named.

# Helpers
have() { command -v "$1" >/dev/null 2>&1; }

compile_test() {
  # compile_test <compiler> <lang>
  local cc="$1" lang="$2"
  local tmpdir src exe
  tmpdir="$(mktemp -d 2>/dev/null || mktemp -d -t ccprobe)"
  src="$tmpdir/t.$lang"
  exe="$tmpdir/a.out"
  if [[ "$lang" == "c" ]]; then
    printf 'int main(void){return 0;}\n' >"$src"
  else
    printf 'int main(){return 0;}\n' >"$src"
  fi
  if "$cc" -x "$lang" "$src" -o "$exe" >/dev/null 2>&1; then
    rm -rf "$tmpdir"
    return 0
  else
    rm -rf "$tmpdir"
    return 1
  fi
}

have_libclang_header() {
  local include_dir pattern

  if have llvm-config; then
    include_dir="$(llvm-config --includedir 2>/dev/null || true)"
    if [[ -n "$include_dir" && -f "$include_dir/clang-c/Index.h" ]]; then
      return 0
    fi
  fi

  if have brew; then
    include_dir="$(brew --prefix llvm 2>/dev/null || true)"
    if [[ -n "$include_dir" && -f "$include_dir/include/clang-c/Index.h" ]]; then
      return 0
    fi
  fi

  for pattern in \
    /usr/include/clang-c/Index.h \
    /usr/local/include/clang-c/Index.h \
    /usr/lib/llvm-*/include/clang-c/Index.h \
    /usr/local/llvm*/include/clang-c/Index.h \
    /opt/homebrew/opt/llvm/include/clang-c/Index.h \
    /usr/local/opt/llvm/include/clang-c/Index.h
  do
    if compgen -G "$pattern" >/dev/null; then
      return 0
    fi
  done
  return 1
}

libclang_install_hint() {
  case "$(uname -s)" in
    Darwin) printf '%s' "brew install llvm" ;;
    FreeBSD) printf '%s' "pkg install llvm" ;;
    Linux) printf '%s' "sudo apt install libclang-dev" ;;
    *) printf '%s' "install the libclang development package" ;;
  esac
}

# Build the unique tools list, preserving order
declare -a tools=()
append_unique() {
  local x
  for x in "$@"; do
    [[ -z "$x" ]] && continue
    local seen=0
    local y
    for y in "${tools[@]:-}"; do
      if [[ "$y" == "$x" ]]; then seen=1; break; fi
    done
    # explicit if (not `(( )) && ...`): a trailing failed arithmetic test
    # would become the function's return value and abort under set -e
    if [[ "$seen" -eq 0 ]]; then
      tools+=("$x")
    fi
  done
  return 0
}
append_unique "cmake" "$c_compiler" "$cxx_compiler" "$clang_format_name" "$clang_tidy_name" "$cppcheck_name"

missing=0

# Simple presence checks
# NOTE: use missing=$((missing+1)), not ((missing++)) — the latter returns a
# failing status when the pre-increment value is 0, which aborts under set -e.
for t in "${tools[@]}"; do
  if ! have "$t"; then
    echo "missing: $t"
    missing=$((missing+1))
  fi
done

# Compiler sanity checks only if named and present
if [[ -n "$c_compiler" ]] && have "$c_compiler"; then
  if ! compile_test "$c_compiler" "c"; then
    echo "broken: $c_compiler (cannot compile a trivial C program)"
    missing=$((missing+1))
  fi
fi
if [[ -n "$cxx_compiler" ]] && have "$cxx_compiler"; then
  if ! compile_test "$cxx_compiler" "c++"; then
    echo "broken: $cxx_compiler (cannot compile a trivial C++ program)"
    missing=$((missing+1))
  fi
fi

# lib_c_facts embeds libclang. A Clang driver alone does not provide the
# public clang-c API headers on package-managed Linux systems, so detect this
# before update-all spends time compiling the repositories that precede it.
if ! have_libclang_header; then
  echo "missing: clang-c/Index.h ($(libclang_install_hint))"
  missing=$((missing+1))
fi

# Validate sanitizer names: a typo (e.g. "adress") would otherwise silently
# build with NO sanitizers, because CMake just skips a missing
# flags/<name>_sanitizer_flags.txt.
if $sanitizers_given && [[ -n "$sanitizers" ]]; then
  _san_rest="$sanitizers,"
  while [[ -n "$_san_rest" ]]; do
    _san_name="${_san_rest%%,*}"
    _san_rest="${_san_rest#*,}"
    # trim surrounding whitespace
    _san_name="${_san_name#"${_san_name%%[![:space:]]*}"}"
    _san_name="${_san_name%"${_san_name##*[![:space:]]}"}"
    [[ -z "$_san_name" ]] && continue
    if [[ ! -f "flags/${_san_name}_sanitizer_flags.txt" ]]; then
      echo "unknown sanitizer: '${_san_name}' (no flags/${_san_name}_sanitizer_flags.txt)"
      missing=$((missing+1))
    fi
  done
fi

# Record sanitizers whenever -s was given — including an explicit -s "",
# which truncates the file so downstream repos really do see "no
# sanitizers" (a stale non-empty file would silently win otherwise).
if $sanitizers_given; then
  printf '%s\n' "$sanitizers" > sanitizers.txt
fi

# Summary and exit code equals number of missing/broken items
if (( missing == 0 )); then
  echo "All required tools OK."
else
  echo "Total missing/broken tools: $missing"
fi

# Cap below 64 so the count can never collide with the usage-error code.
if (( missing > 63 )); then
  missing=63
fi

exit "$missing"
