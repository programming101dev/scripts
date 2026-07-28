#!/usr/bin/env bash
# test-cmake.sh — regression harness for the shared CMakeLists.txt
#
# Configures (and where sensible, builds) a matrix of tiny sample projects
# against the CMakeLists.txt in this directory, asserting the outcomes that
# past bugs have violated:
#
#   exe-simple         configure+build succeed (baseline pipeline:
#                      compile -> analyze -> tidy -> cppcheck)
#   missing-flags      no .flags/<compiler> cache -> configure MUST fail
#                      unless the caller explicitly opts out
#   no-flags           P101_NO_FLAGS suppresses probed flags when a cache exists
#   lib-exe-shared     a source listed in BOTH a library and an executable,
#                      plus a header given as a RELATIVE path
#                      (regressions: duplicate stamp OUTPUT was a fatal
#                      configure error; relative headers skipped format)
#   whitespace-targets whitespace/empty entries in *_TARGETS lists must be
#                      filtered, not treated as targets (regression: CMake
#                      regex has no \s)
#   zero-targets       a config.cmake with no targets must still configure
#                      (regression: bare add_dependencies was fatal)
#   missing-config     no config.cmake -> configure MUST fail
#   out-of-tree        an absolute source outside the project dir MUST fail
#                      configure (regression: out-of-tree paths were silently
#                      skipped instead of flagged as a config.cmake error)
#   tidy-gate          code with a tidy diagnostic (magic number) -> build
#                      MUST fail, and for that reason (proves the analysis
#                      gate actually gates)
#   cxx-exe            C++ variant of the baseline (only when a C++
#                      compiler is available)
#   ctu                a cross-TU div-by-zero (main() passes 0 into a divide
#                      in another file) MUST fail the build via CTU — only
#                      when clang + clang-extdef-mapping are present; the
#                      per-file stages cannot see it. Skipped otherwise.
#
# Portable: macOS (stock bash 3.2), Linux, FreeBSD.
#
# Usage: ./test-cmake.sh [-c <C compiler>] [-x <C++ compiler>] [-k] [-h]
#   -c  C compiler to test with   (default: first of gcc, clang, cc found)
#   -x  C++ compiler to test with (default: first of g++, clang++, c++)
#   -k  keep the sandbox directory for inspection (printed at exit)
#   -h  help
# Exit status: 0 all cases passed, 1 failures, 2 missing prerequisites.

set -euo pipefail

CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$PWD"
CMAKE_FILE="${SCRIPT_DIR}/CMakeLists.txt"

c_compiler=""
cxx_compiler=""
keep=false

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

# --help / -h -> usage, exit 0 (P101 uniform CLI help)
case " $* " in *" --help "*|*" -h "*) ( usage ) || true; exit 0 ;; esac

while getopts ":c:x:kh" opt; do
  case "$opt" in
    c) c_compiler="$OPTARG" ;;
    x) cxx_compiler="$OPTARG" ;;
    k) keep=true ;;
    h) usage 0 ;;
    \?) echo "Invalid option: -$OPTARG" >&2; usage 2 ;;
    :)  echo "Option -$OPTARG requires an argument." >&2; usage 2 ;;
  esac
done

have() { command -v "$1" >/dev/null 2>&1; }

# ---------- pick compilers ----------
if [[ -z "$c_compiler" ]]; then
  for cand in gcc clang cc; do
    if have "$cand"; then c_compiler="$cand"; break; fi
  done
fi
if [[ -z "$cxx_compiler" ]]; then
  for cand in g++ clang++ c++; do
    if have "$cand"; then cxx_compiler="$cand"; break; fi
  done
fi

# ---------- prerequisites (same tools the real builds REQUIRE) ----------
missing=0
for t in cmake clang-tidy cppcheck; do
  if ! have "$t"; then echo "missing prerequisite: $t" >&2; missing=$((missing+1)); fi
done
if [[ -z "$c_compiler" ]] || ! have "$c_compiler"; then
  echo "missing prerequisite: a C compiler (tried gcc, clang, cc)" >&2
  missing=$((missing+1))
fi
[[ -f "$CMAKE_FILE" ]] || { echo "missing: $CMAKE_FILE" >&2; missing=$((missing+1)); }
if (( missing > 0 )); then
  echo "Cannot run: $missing prerequisite(s) missing." >&2
  exit 2
fi
if ! have clang-format; then
  echo "note: clang-format not found; the format stage will be skipped by CMake (as in real builds)."
fi

# ---------- sandbox ----------
SANDBOX="$(mktemp -d 2>/dev/null || mktemp -d -t p101cmaketest)"
cleanup() {
  if $keep; then
    echo "Sandbox kept: $SANDBOX"
  else
    rm -rf "$SANDBOX" 2>/dev/null || true
  fi
}
trap cleanup EXIT

pass=0
fail=0

ok()   { echo "PASS: $1"; pass=$((pass+1)); }
bad()  { echo "FAIL: $1"; [[ -n "${2:-}" && -f "${2:-}" ]] && tail -15 "$2" | sed 's/^/    | /'; fail=$((fail+1)); }

# new_proj <name> — creates $PROJ with the shared CMakeLists.txt + .clang-format
new_proj() {
  PROJ="$SANDBOX/$1"
  mkdir -p "$PROJ/src" "$PROJ/include"
  cp "$CMAKE_FILE" "$PROJ/CMakeLists.txt"
  # the shared CMakeLists now sources its helpers from cmake/ — mirror the
  # workspace symlink so the harness exercises the real layout
  ln -sfn "$(CDPATH= cd "$(dirname "$CMAKE_FILE")" && pwd)/cmake" "$PROJ/cmake"
  mkdir -p "$PROJ/.flags/$(basename "$c_compiler")"
  if [[ -n "$cxx_compiler" ]]; then
    mkdir -p "$PROJ/.flags/$(basename "$cxx_compiler")"
  fi
  printf 'BasedOnStyle: LLVM\nIndentWidth: 4\n' > "$PROJ/.clang-format"
}

# configure <proj> [extra cmake args...] ; rc in $RC, log in <proj>/configure.log
configure() {
  local p="$1"; shift
  RC=0
  cmake -S "$p" -B "$p/build" -DCMAKE_C_COMPILER="$c_compiler" "$@" \
    > "$p/configure.log" 2>&1 || RC=$?
}

build() {
  local p="$1"
  RC=0
  cmake --build "$p/build" > "$p/build.log" 2>&1 || RC=$?
}

# Common tidy-clean C bits
write_c_config_exe() {
  # $1 = proj dir
  cat > "$1/config.cmake" <<'EOF'
set(PROJECT_NAME sample)
set(PROJECT_VERSION 1.0.0)
set(PROJECT_DESCRIPTION "harness sample")
set(PROJECT_LANGUAGE C)
set(STANDARD_FLAGS -std=c17 -Werror)
set(EXECUTABLE_TARGETS hello)
set(hello_SOURCES src/main.c)
EOF
}

write_clean_main_c() {
  printf '#include "util.h"\nint main(void)\n{\n    return util_value();\n}\n' > "$1/src/main.c"
}

write_clean_util_c() {
  printf '#include "util.h"\nint util_value(void)\n{\n    return 0;\n}\n' > "$1/src/util.c"
  printf '#ifndef UTIL_H\n#define UTIL_H\nint util_value(void);\n#endif\n' > "$1/include/util.h"
}

echo "== test-cmake.sh: C compiler=$c_compiler  C++ compiler=${cxx_compiler:-<none>} =="
echo

# ---------- case: exe-simple ----------
new_proj exe-simple
write_c_config_exe "$PROJ"
printf 'int main(void)\n{\n    return 0;\n}\n' > "$PROJ/src/main.c"
configure "$PROJ"
if (( RC == 0 )); then
  build "$PROJ"
  if (( RC == 0 )); then ok "exe-simple: configure+build (full analysis pipeline)"
  else bad "exe-simple: build failed" "$PROJ/build.log"; fi
else
  bad "exe-simple: configure failed" "$PROJ/configure.log"
fi

# ---------- case: missing-flags env gate ----------
# A normal project must not silently configure without its probed flag cache.
new_proj missing-flags
write_c_config_exe "$PROJ"
printf 'int main(void)\n{\n    return 0;\n}\n' > "$PROJ/src/main.c"
rm -rf "$PROJ/.flags/$(basename "$c_compiler")"
configure "$PROJ"
if (( RC != 0 )) && grep -q 'No flags dir' "$PROJ/configure.log"; then
  ok "missing-flags: configure fails without .flags/<compiler>"
else
  bad "missing-flags: configure did not fail on missing .flags/<compiler>" "$PROJ/configure.log"
fi

rm -rf "$PROJ/build"
P101_ALLOW_NO_FLAGS=1 configure "$PROJ"
if (( RC == 0 )) && grep -q 'P101_ALLOW_NO_FLAGS is set' "$PROJ/configure.log"; then
  ok "missing-flags(opt-out): explicit P101_ALLOW_NO_FLAGS permits bring-up configure"
else
  bad "missing-flags(opt-out): explicit opt-out did not permit configure" "$PROJ/configure.log"
fi

# ---------- case: no-flags env gate ----------
# P101_NO_FLAGS in the environment must suppress probed flags + sanitizers
# and still configure cleanly. A seeded .flags dir proves suppression: with
# the var UNSET the flag is loaded; with it SET the configure reports the
# empty flag dir and does not load it.
new_proj no-flags
write_c_config_exe "$PROJ"
printf 'int main(void)\n{\n    return 0;\n}\n' > "$PROJ/src/main.c"
mkdir -p "$PROJ/.flags/$(basename "$c_compiler")"
printf '%s' "-DHARNESS_SENTINEL=1" > "$PROJ/.flags/$(basename "$c_compiler")/code_generation_flags.txt"
# unset -> flag dir is loaded
unset P101_NO_FLAGS
configure "$PROJ"
RC_A=$RC
grep -q "Flag dirs used:.*\.flags/$(basename "$c_compiler")" "$PROJ/configure.log" && loaded_when_unset=1 || loaded_when_unset=0
# set -> flag dir suppressed, configure still succeeds
rm -rf "$PROJ/build"
P101_NO_FLAGS=1 configure "$PROJ"
RC_B=$RC
if grep -q "P101_NO_FLAGS set" "$PROJ/configure.log" \
     && grep -q "Flag dirs used: *$" "$PROJ/configure.log"; then suppressed_when_set=1; else suppressed_when_set=0; fi
if (( RC_A == 0 && RC_B == 0 && loaded_when_unset == 1 && suppressed_when_set == 1 )); then
  ok "no-flags: P101_NO_FLAGS suppresses flags, configure still succeeds"
else
  bad "no-flags: env gate (unset-rc=$RC_A unset-loaded=$loaded_when_unset set-rc=$RC_B set-suppressed=$suppressed_when_set)" "$PROJ/configure.log"
fi

# ---------- case: lib-exe-shared ----------
new_proj lib-exe-shared
cat > "$PROJ/config.cmake" <<'EOF'
set(PROJECT_NAME shared)
set(PROJECT_VERSION 1.0.0)
set(PROJECT_DESCRIPTION "harness sample")
set(PROJECT_LANGUAGE C)
set(STANDARD_FLAGS -std=c17 -Werror)
set(LIBRARY_TARGETS mylib)
set(EXECUTABLE_TARGETS hello)
set(mylib_SOURCES src/util.c)
set(mylib_HEADERS include/util.h)
set(hello_SOURCES src/main.c src/util.c)
EOF
write_clean_util_c "$PROJ"
write_clean_main_c "$PROJ"
configure "$PROJ"
if (( RC == 0 )); then
  build "$PROJ"
  if (( RC == 0 )); then ok "lib-exe-shared: shared source + relative header"
  else bad "lib-exe-shared: build failed" "$PROJ/build.log"; fi
else
  bad "lib-exe-shared: configure failed (duplicate stamp regression?)" "$PROJ/configure.log"
fi

# ---------- case: whitespace-targets ----------
new_proj whitespace-targets
cat > "$PROJ/config.cmake" <<'EOF'
set(PROJECT_NAME wsproj)
set(PROJECT_VERSION 1.0.0)
set(PROJECT_DESCRIPTION "harness sample")
set(PROJECT_LANGUAGE C)
set(STANDARD_FLAGS -std=c17 -Werror)
set(LIBRARY_TARGETS " ")
set(EXECUTABLE_TARGETS hello "")
set(hello_SOURCES src/main.c)
EOF
printf 'int main(void)\n{\n    return 0;\n}\n' > "$PROJ/src/main.c"
configure "$PROJ"
if (( RC == 0 )) && ! grep -q 'has no <name>_SOURCES' "$PROJ/configure.log"; then
  ok "whitespace-targets: blank entries filtered"
else
  bad "whitespace-targets: blank entry treated as a target" "$PROJ/configure.log"
fi

# ---------- case: zero-targets ----------
new_proj zero-targets
cat > "$PROJ/config.cmake" <<'EOF'
set(PROJECT_NAME emptyproj)
set(PROJECT_VERSION 1.0.0)
set(PROJECT_DESCRIPTION "harness sample")
set(PROJECT_LANGUAGE C)
set(STANDARD_FLAGS -std=c17 -Werror)
EOF
configure "$PROJ"
if (( RC == 0 )); then ok "zero-targets: configure succeeds"
else bad "zero-targets: configure failed" "$PROJ/configure.log"; fi

# ---------- case: missing-config ----------
new_proj missing-config
configure "$PROJ"
if (( RC != 0 )) && grep -q 'config.cmake not found' "$PROJ/configure.log"; then
  ok "missing-config: configure fails with clear message"
else
  bad "missing-config: expected configure failure mentioning config.cmake" "$PROJ/configure.log"
fi

# ---------- case: out-of-tree ----------
# A source that resolves OUTSIDE the project directory must be a hard error,
# not silently skipped (an out-of-tree path almost always means a config.cmake
# mistake). The gate fires at configure time in the format/tidy walk.
new_proj out-of-tree
outside_src="$SANDBOX/outside_main.c"
printf 'int main(void)\n{\n    return 0;\n}\n' > "$outside_src"
cat > "$PROJ/config.cmake" <<EOF
set(PROJECT_NAME ootproj)
set(PROJECT_VERSION 1.0.0)
set(PROJECT_DESCRIPTION "harness sample")
set(PROJECT_LANGUAGE C)
set(STANDARD_FLAGS -std=c17 -Werror)
set(EXECUTABLE_TARGETS hello)
set(hello_SOURCES ${outside_src})
EOF
configure "$PROJ"
# CMake word-wraps message() text, so the full phrase can span two lines;
# grep a fragment that stays intact on one line.
if (( RC != 0 )) && grep -q 'outside the project' "$PROJ/configure.log"; then
  ok "out-of-tree: absolute source outside project is rejected"
elif (( RC != 0 )); then
  bad "out-of-tree: configure failed but not from the out-of-tree gate" "$PROJ/configure.log"
else
  bad "out-of-tree: out-of-tree source was accepted (gate is broken)" "$PROJ/configure.log"
fi

# ---------- case: tidy-gate ----------
new_proj tidy-gate
write_c_config_exe "$PROJ"
# 42 is reliably flagged by readability-magic-numbers with -warnings-as-errors=*
printf 'int main(void)\n{\n    return 42;\n}\n' > "$PROJ/src/main.c"
configure "$PROJ"
if (( RC == 0 )); then
  build "$PROJ"
  if (( RC != 0 )) && grep -qi 'magic number' "$PROJ/build.log"; then
    ok "tidy-gate: bad code is rejected by clang-tidy"
  elif (( RC != 0 )); then
    bad "tidy-gate: build failed but not from the tidy diagnostic" "$PROJ/build.log"
  else
    bad "tidy-gate: bad code passed the pipeline (gate is broken)" "$PROJ/build.log"
  fi
else
  bad "tidy-gate: configure failed" "$PROJ/configure.log"
fi

# ---------- case: cxx-exe (optional) ----------
if [[ -n "$cxx_compiler" ]] && have "$cxx_compiler"; then
  new_proj cxx-exe
  cat > "$PROJ/config.cmake" <<'EOF'
set(PROJECT_NAME cxxsample)
set(PROJECT_VERSION 1.0.0)
set(PROJECT_DESCRIPTION "harness sample")
set(PROJECT_LANGUAGE CXX)
set(STANDARD_FLAGS -std=c++20)
set(EXECUTABLE_TARGETS hello)
set(hello_SOURCES src/main.cpp)
EOF
  # trailing return type: checks=* includes modernize-use-trailing-return-type
  printf 'auto main() -> int\n{\n    return 0;\n}\n' > "$PROJ/src/main.cpp"
  configure "$PROJ" -DCMAKE_CXX_COMPILER="$cxx_compiler"
  if (( RC == 0 )); then
    build "$PROJ"
    if (( RC == 0 )); then ok "cxx-exe: C++ configure+build"
    else bad "cxx-exe: build failed" "$PROJ/build.log"; fi
  else
    bad "cxx-exe: configure failed" "$PROJ/configure.log"
  fi
else
  echo "note: no C++ compiler found; skipping cxx-exe case."
fi

# ---------- case: ctu (cross-TU analysis, optional) ----------
# CTU catches a bug that only exists across a call boundary. It engages only
# with clang + a matching clang-extdef-mapping (same condition the CMakeLists
# uses); skip otherwise. A cross-TU div-by-zero is invisible to the compile,
# per-file tidy, and per-file --analyze stages — only CTU sees it.
_ctu_tool=""
if [[ "$c_compiler" == *clang* ]] && have "$c_compiler"; then
  _ccbin="$(command -v "$c_compiler")"
  _ccreal="$(readlink -f "$_ccbin" 2>/dev/null || echo "$_ccbin")"
  for _c in "$(dirname "$_ccreal")/clang-extdef-mapping" \
            "$(dirname "$_ccbin")/clang-extdef-mapping" \
            "$(dirname "$_ccbin")"/clang-extdef-mapping-*; do
    [[ -x "$_c" ]] && { _ctu_tool="$_c"; break; }
  done
fi
if [[ -n "$_ctu_tool" ]]; then
  new_proj ctu
  cat > "$PROJ/config.cmake" <<'EOF'
set(PROJECT_NAME ctusample)
set(PROJECT_VERSION 1.0.0)
set(PROJECT_DESCRIPTION "harness sample")
set(PROJECT_LANGUAGE C)
set(STANDARD_FLAGS -std=c17 -Werror)
set(EXECUTABLE_TARGETS app)
set(app_SOURCES src/main.c src/lib.c)
EOF
  # numerator 1 (not a magic number, so earlier tidy stage passes); the bug is
  # the cross-TU division by the 0 that main() passes in.
  printf '#ifndef CTU_LIB_H\n#define CTU_LIB_H\nint compute(int d);\n#endif\n' > "$PROJ/src/lib.h"
  printf '#include "lib.h"\nint main(void)\n{\n    return compute(0);\n}\n' > "$PROJ/src/main.c"
  printf '#include "lib.h"\nint compute(int d)\n{\n    return 1 / d;\n}\n' > "$PROJ/src/lib.c"
  configure "$PROJ"
  if (( RC == 0 )); then
    build "$PROJ"
    # DivideZero is the CSA/CTU checker name (cppcheck words it differently),
    # so this specifically proves the CTU stage caught the cross-TU bug.
    if (( RC != 0 )) && grep -qi 'DivideZero' "$PROJ/build.log"; then
      ok "ctu: cross-TU division-by-zero caught by CTU"
    elif (( RC != 0 )); then
      bad "ctu: build failed but not from the CTU DivideZero finding" "$PROJ/build.log"
    else
      bad "ctu: cross-TU bug NOT caught though CTU engaged" "$PROJ/build.log"
    fi
  else
    bad "ctu: configure failed" "$PROJ/configure.log"
  fi
else
  echo "note: no clang + clang-extdef-mapping; skipping CTU case."
fi

# ---------- case: file-flag-optout ----------
# A per-file opt-out disables a flag for ONLY the named file, keeping it on
# everywhere else. Exercised with -fharden-control-flow-redundancy (gcc 14+),
# which probes fine but hard-errors on any function that calls setjmp. Needs a
# gcc that actually has the flag; skipped otherwise.
_sjsrc='#include <setjmp.h>\nint main(void)\n{\n    jmp_buf b;\n\n    if(setjmp(b))\n    {\n        return 1;\n    }\n    return 0;\n}\n'
if [[ "$c_compiler" == *gcc* ]] && have "$c_compiler" \
   && printf 'int main(void){return 0;}\n' | "$c_compiler" -fharden-control-flow-redundancy -Werror -x c -c - -o /dev/null 2>/dev/null; then
  _ccbase="$(basename "$c_compiler")"
  # with the opt-out: builds clean
  new_proj file-optout
  mkdir -p "$PROJ/.flags/$_ccbase"
  printf -- '-fharden-control-flow-redundancy\n' > "$PROJ/.flags/$_ccbase/instrumentation_compiler.txt"
  cat > "$PROJ/config.cmake" <<'EOF'
set(PROJECT_NAME optout)
set(PROJECT_VERSION 1.0.0)
set(PROJECT_DESCRIPTION "harness sample")
set(PROJECT_LANGUAGE C)
set(STANDARD_FLAGS -std=c17 -Werror)
set(EXECUTABLE_TARGETS app)
set(app_SOURCES src/main.c)
set(P101_FILE_FLAG_OPTOUTS "src/main.c -fharden-control-flow-redundancy")
EOF
  printf "$_sjsrc" > "$PROJ/src/main.c"
  configure "$PROJ"
  if (( RC == 0 )); then
    build "$PROJ"
    if (( RC == 0 )); then ok "file-flag-optout: setjmp file opts out of fhardcfr, builds clean"
    else bad "file-flag-optout: build failed despite opt-out" "$PROJ/build.log"; fi
  else
    bad "file-flag-optout: configure failed" "$PROJ/configure.log"
  fi
  # control: WITHOUT the opt-out the same file MUST fail on fhardcfr
  new_proj file-optout-ctl
  mkdir -p "$PROJ/.flags/$_ccbase"
  printf -- '-fharden-control-flow-redundancy\n' > "$PROJ/.flags/$_ccbase/instrumentation_compiler.txt"
  cat > "$PROJ/config.cmake" <<'EOF'
set(PROJECT_NAME optoutctl)
set(PROJECT_VERSION 1.0.0)
set(PROJECT_DESCRIPTION "harness sample")
set(PROJECT_LANGUAGE C)
set(STANDARD_FLAGS -std=c17 -Werror)
set(EXECUTABLE_TARGETS app)
set(app_SOURCES src/main.c)
EOF
  printf "$_sjsrc" > "$PROJ/src/main.c"
  configure "$PROJ"
  if (( RC == 0 )); then
    build "$PROJ"
    if (( RC != 0 )) && grep -qi 'harden-control-flow-redundancy' "$PROJ/build.log"; then
      ok "file-flag-optout(control): without opt-out the setjmp file fails as expected"
    elif (( RC != 0 )); then
      bad "file-flag-optout(control): failed but not from fhardcfr" "$PROJ/build.log"
    else
      bad "file-flag-optout(control): expected fhardcfr failure but built clean" "$PROJ/build.log"
    fi
  else
    bad "file-flag-optout(control): configure failed" "$PROJ/configure.log"
  fi
else
  echo "note: no gcc with -fharden-control-flow-redundancy; skipping file-flag-optout case."
fi

# Coverage/profiling are now applied from PROBED per-compiler buckets
# (.flags/<cc>/coverage_flags.txt / profile_flags.txt), read only when the
# user selects them. Scaffold a minimal probed cache so the selection-driven
# reads in the CMakeLists have something to load (real builds get this from
# generate-flags.sh). Bare flags — the reader tokenizes with separate_arguments.
scaffold_instrumentation() {
  local proj="$1" ccbase
  ccbase="$(basename "$c_compiler")"
  mkdir -p "$proj/.flags/$ccbase"
  printf -- '--coverage\n' > "$proj/.flags/$ccbase/coverage_flags.txt"
  printf -- '-pg\n'         > "$proj/.flags/$ccbase/profile_flags.txt"
}

# ---------- case: coverage opt-in via --coverage/P101_COVERAGE env ----------
# Off by default; when selected the build instruments with the probed coverage
# flags and emits a .gcno per TU. A normal build must NOT emit one.
new_proj coverage
write_c_config_exe "$PROJ"
scaffold_instrumentation "$PROJ"
printf 'int main(void)\n{\n    return 0;\n}\n' > "$PROJ/src/main.c"
P101_COVERAGE=1 configure "$PROJ"
if (( RC == 0 )); then
  P101_COVERAGE=1 build "$PROJ"
  if (( RC == 0 )) && find "$PROJ/build" -name '*.gcno' 2>/dev/null | grep -q .; then
    ok "coverage(env): P101_COVERAGE reads probed coverage_flags.txt, emits .gcno"
  elif (( RC == 0 )); then
    bad "coverage(env): built but no .gcno (coverage not applied)" "$PROJ/build.log"
  else
    bad "coverage(env): build failed" "$PROJ/build.log"
  fi
else
  bad "coverage(env): configure failed" "$PROJ/configure.log"
fi

# ---------- case: coverage opt-in via coverage.txt sentinel file ----------
# The file-based selector (mirrors sanitizers.txt) must enable coverage with
# no env var set.
new_proj coverage-file
write_c_config_exe "$PROJ"
scaffold_instrumentation "$PROJ"
printf '# 1 = on\n1\n' > "$PROJ/coverage.txt"
printf 'int main(void)\n{\n    return 0;\n}\n' > "$PROJ/src/main.c"
configure "$PROJ" && build "$PROJ"
if (( RC == 0 )) && find "$PROJ/build" -name '*.gcno' 2>/dev/null | grep -q .; then
  ok "coverage(file): coverage.txt=1 (no env) enables coverage, emits .gcno"
else
  bad "coverage(file): coverage.txt did not enable coverage" "$PROJ/build.log"
fi

# control: a normal build (flags scaffolded but NOT selected) must not be
# coverage-instrumented
new_proj coverage-off
write_c_config_exe "$PROJ"
scaffold_instrumentation "$PROJ"
printf 'int main(void)\n{\n    return 0;\n}\n' > "$PROJ/src/main.c"
configure "$PROJ" && build "$PROJ"
if (( RC == 0 )) && ! find "$PROJ/build" -name '*.gcno' 2>/dev/null | grep -q .; then
  ok "coverage(control): unselected build is not coverage-instrumented"
else
  bad "coverage(control): unselected build unexpectedly has .gcno (opt-in leaked)" "$PROJ/build.log"
fi

# ---------- case: profiling opt-in (P101_PROFILE) ----------
new_proj profile
write_c_config_exe "$PROJ"
scaffold_instrumentation "$PROJ"
printf 'int main(void)\n{\n    return 0;\n}\n' > "$PROJ/src/main.c"
P101_PROFILE=1 configure "$PROJ"
if (( RC == 0 )); then
  P101_PROFILE=1 build "$PROJ"
  profile_compile_db="$PROJ/build/compile_commands.json"
  if (( RC == 0 )) && [ -f "$profile_compile_db" ] && grep -q -- '-pg' "$profile_compile_db"; then
    ok "profile: P101_PROFILE reads probed profile_flags.txt, instruments with -pg"
  elif (( RC == 0 )); then
    bad "profile: built but -pg not in compile_commands.json" "$PROJ/build.log"
  else
    bad "profile: build failed" "$PROJ/build.log"
  fi
else
  bad "profile: configure failed" "$PROJ/configure.log"
fi

# ---------- summary ----------
echo
echo "== test-cmake.sh: $pass passed, $fail failed =="
if (( fail > 0 )); then
  $keep || echo "(re-run with -k to keep the sandbox and inspect logs)"
  exit 1
fi
exit 0
