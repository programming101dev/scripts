#!/usr/bin/env bash
# test-cmake.sh — regression harness for the shared CMakeLists.txt
#
# Configures (and where sensible, builds) a matrix of tiny sample projects
# against the CMakeLists.txt in this directory, asserting the outcomes that
# past bugs have violated:
#
#   exe-simple         configure+build succeed (baseline pipeline:
#                      compile -> analyze -> tidy -> cppcheck); a no-op build
#                      reuses quality receipts, while a source edit invalidates
#                      all three dependency-tracked quality stages
#   runtime-only       consumer artifact builds without rerunning the analyzer
#                      pipeline and rejects sanitizer-bearing configurations
#   runtime-link       keyed runtime-only consumers resolve only the exact
#                      compiler-pair sibling artifact, never stale fallbacks
#   macos-asan-order   a sanitized executable records its compiler-matched ASan
#                      runtime before user shared libraries
#   missing-flags      no .flags/<compiler> cache -> configure MUST fail
#                      unless the caller explicitly opts out
#   no-flags           P101_NO_FLAGS suppresses probed flags when a cache exists
#   flag-cache-refresh changing a probed cache file automatically reconfigures
#                      an existing build tree before compilation
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

CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
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

run_with_timeout() {
  local seconds="$1"
  local process_id
  local status
  local watchdog_id
  shift

  "$@" &
  process_id=$!
  (
    sleep "$seconds"
    kill -TERM "$process_id" 2>/dev/null || true
  ) &
  watchdog_id=$!
  if wait "$process_id"; then
    status=0
  else
    status=$?
  fi
  kill -TERM "$watchdog_id" 2>/dev/null || true
  wait "$watchdog_id" 2>/dev/null || true
  return "$status"
}

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
for t in cmake clang-format clang-tidy cppcheck; do
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
bad()  { echo "FAIL: $1"; [[ -n "${2:-}" && -f "${2:-}" ]] && sed 's/^/    | /' "$2"; fail=$((fail+1)); }

# new_proj <name> — creates $PROJ with the shared CMakeLists.txt + .clang-format
new_proj() {
  PROJ="$SANDBOX/$1"
  mkdir -p "$PROJ/src" "$PROJ/include"
  cp "$CMAKE_FILE" "$PROJ/CMakeLists.txt"
  # The shared CMakeLists now sources its helpers from cmake/.  Copy the exact
  # helper bytes into the fixture and normalize their mtimes to the guest
  # clock.  GitHub's FreeBSD VM source synchronization can leave host-written
  # files slightly in the future, which otherwise makes a genuine no-op build
  # reconfigure and rerun every quality stage.
  mkdir -p "$PROJ/cmake"
  cp -R "$(CDPATH='' cd "$(dirname "$CMAKE_FILE")" && pwd)/cmake/." "$PROJ/cmake"
  find "$PROJ/cmake" -type f -exec touch {} +
  mkdir -p "$PROJ/.flags/$(basename "$c_compiler")"
  if [[ -n "$cxx_compiler" ]]; then
    mkdir -p "$PROJ/.flags/$(basename "$cxx_compiler")"
  fi
  printf 'BasedOnStyle: LLVM\nIndentWidth: 4\nBreakBeforeBraces: Allman\nAllowShortFunctionsOnASingleLine: None\n' > "$PROJ/.clang-format"
}

# configure <proj> [extra cmake args...] ; rc in $RC, log in <proj>/configure.log
configure() {
  local p="$1"; shift
  RC=0
  cmake -S "$p" -B "$p/build" -DCMAKE_C_COMPILER="$c_compiler" \
    -DP101_BUILD_LEVEL=3 "$@" \
    > "$p/configure.log" 2>&1 || RC=$?
}

build() {
  local p="$1"
  RC=0
  MAKEFLAGS='' MFLAGS='' MAKELEVEL=0 \
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
  if (( RC == 0 )); then
    ok "exe-simple: configure+build (full analysis pipeline)"
    # Some VM images install CMake module files with timestamps ahead of the
    # guest clock.  Without this fixture-local normalization, CMake regenerates
    # its Makefile on the next build and the test measures image clock skew
    # instead of dependency-tracked quality stages.  The later source edit is
    # dated 2030, so it remains newer and still exercises invalidation.
    # Normalize the whole fixture after the first build. Source archives and
    # FreeBSD VM clocks can otherwise leave dependency files newer than their
    # generated targets, causing a false non-no-op relink and quality rerun.
    find "$PROJ" -type f -exec touch -t 202901010000 {} +
    RC=0
    MAKEFLAGS='' MFLAGS='' MAKELEVEL=0 \
      cmake --build "$PROJ/build" --verbose > "$PROJ/no-op-build.log" 2>&1 || RC=$?
    if (( RC == 0 )) &&
       ! grep -Eq 'Per-TU analyze stage|Generating \.p101-quality/clang-tidy\.stamp|cppcheck over entire project' \
         "$PROJ/no-op-build.log"; then
      ok "exe-simple: no-op build reuses dependency-tracked quality stages"
    else
      bad "exe-simple: no-op build reran a quality stage" "$PROJ/no-op-build.log"
    fi

    printf 'int main(void)\n{\n    /* Trigger dependency invalidation without changing behavior. */\n    return 0;\n}\n' \
      > "$PROJ/src/main.c"
    touch -t 203001010000 "$PROJ/src/main.c"
    RC=0
    MAKEFLAGS='' MFLAGS='' MAKELEVEL=0 \
      cmake --build "$PROJ/build" --verbose > "$PROJ/changed-build.log" 2>&1 || RC=$?
    if (( RC == 0 )) &&
       grep -q 'Per-TU analyze stage' "$PROJ/changed-build.log" &&
       grep -q 'Generating \.p101-quality/clang-tidy\.stamp' \
         "$PROJ/changed-build.log" &&
       grep -q 'cppcheck over entire project' "$PROJ/changed-build.log"; then
      ok "exe-simple: source edit invalidates every quality stage"
    else
      bad "exe-simple: source edit did not invalidate every quality stage" \
        "$PROJ/changed-build.log"
    fi
  else
    bad "exe-simple: build failed" "$PROJ/build.log"
  fi
else
  bad "exe-simple: configure failed" "$PROJ/configure.log"
fi

# ---------- case: instrumentation-free runtime artifact ----------
new_proj runtime-only
write_c_config_exe "$PROJ"
printf '1\n' > "$PROJ/coverage.txt"
printf '1\n' > "$PROJ/profile.txt"
printf 'int main(void)\n{\n    return 0;\n}\n' > "$PROJ/src/main.c"
configure "$PROJ" -DP101_RUNTIME_ONLY=ON \
  -DP101_BUILD_LEVEL=1 \
  -DP101_DISABLE_INSTRUMENTATION=ON \
  -DP101_COVERAGE_MODE=OFF -DP101_PROFILE_MODE=OFF \
  -DSANITIZER_LIST=
if (( RC == 0 )); then
  build "$PROJ"
  if (( RC == 0 )) &&
     grep -q 'P101 build level 1: checking formatting and building an instrumentation-free install artifact' "$PROJ/configure.log" &&
     grep -q 'checking:.*clang-format' "$PROJ/build.log" &&
     ! grep -Eq 'Coverage selected|Profiling selected' "$PROJ/configure.log" &&
     ! grep -Eq 'clang-tidy|cppcheck|static analyzer|analyze stage' "$PROJ/build.log" &&
     ! grep -Eq -- '--coverage|-pg' "$PROJ/build/compile_commands.json"; then
    ok "runtime-only: builds clean primary artifact despite repository instrumentation selectors"
  elif (( RC == 0 )); then
    bad "runtime-only: quality-analysis target ran in consumer build" "$PROJ/build.log"
  else
    bad "runtime-only: build failed" "$PROJ/build.log"
  fi
else
  bad "runtime-only: configure failed" "$PROJ/configure.log"
fi

# ---------- cases: explicit build levels ----------
new_proj quick-level
write_c_config_exe "$PROJ"
printf 'int main(void)\n{\n    return 0;\n}\n' > "$PROJ/src/main.c"
printf '#!/usr/bin/env bash\ntest "${P101_TEST_MAIN_BUILD:-}" = "%s/build"\nprintf tested > "%s/test-ran"\n' \
  "$PROJ" "$PROJ" > "$PROJ/test.sh"
RC=0
cmake -S "$PROJ" -B "$PROJ/build" -DCMAKE_C_COMPILER="$c_compiler" \
  > "$PROJ/configure.log" 2>&1 || RC=$?
if (( RC == 0 )); then
  build "$PROJ"
  if (( RC == 0 )) && [[ ! -e "$PROJ/test-ran" ]] &&
     grep -Fq 'P101_BUILD_LEVEL:STRING=1' "$PROJ/build/CMakeCache.txt" &&
     grep -q 'checking:.*clang-format' "$PROJ/build.log" &&
     ! grep -Eq 'clang-tidy|cppcheck|analyze stage' "$PROJ/build.log"; then
    ok "level-1: default checks formatting and builds without tests or analyzers"
  else
    bad "level-1: ran work outside the compile/install contract" "$PROJ/build.log"
  fi
else
  bad "level-1: configure failed" "$PROJ/configure.log"
fi

new_proj medium-level
write_c_config_exe "$PROJ"
printf 'int main(void)\n{\n    return 0;\n}\n' > "$PROJ/src/main.c"
printf '#!/usr/bin/env bash\nprintf tested > "%s/test-ran"\n' "$PROJ" \
  > "$PROJ/test.sh"
configure "$PROJ" -DP101_BUILD_LEVEL=2
if (( RC == 0 )); then
  build "$PROJ"
  if (( RC == 0 )) && [[ -f "$PROJ/test-ran" ]] &&
     grep -q 'checking:.*clang-format' "$PROJ/build.log" &&
     ! grep -Eq 'clang-tidy|cppcheck|analyze stage' "$PROJ/build.log"; then
    ok "level-2: retains formatting and adds repository unit tests without full analyzers"
  else
    bad "level-2: did not enforce the medium contract" "$PROJ/build.log"
  fi
else
  bad "level-2: configure failed" "$PROJ/configure.log"
fi

# ---------- case: runtime-only sibling dependency precedence ----------
runtime_root="$SANDBOX/runtime-link-workspace"
runtime_dep="$runtime_root/libraries/lib_dep"
PROJ="$runtime_root/libraries/lib_consumer"
quality_build_key="matrix-quality"
runtime_build_key="matrix-runtime"
quality_dep_build="$runtime_dep/build-${quality_build_key}"
runtime_dep_build="$runtime_dep/build-${runtime_build_key}"
decoy_runtime_build="$runtime_dep/build-decoy-runtime"
mkdir -p "$runtime_dep/include" "$quality_dep_build" "$runtime_dep_build" \
  "$decoy_runtime_build"
mkdir -p "$PROJ/src" "$PROJ/include" "$PROJ/.flags/$(basename "$c_compiler")"
cp "$CMAKE_FILE" "$PROJ/CMakeLists.txt"
ln -sfn "$(CDPATH='' cd "$(dirname "$CMAKE_FILE")" && pwd)/cmake" "$PROJ/cmake"
printf 'BasedOnStyle: LLVM\nIndentWidth: 4\nBreakBeforeBraces: Allman\nAllowShortFunctionsOnASingleLine: None\n' > "$PROJ/.clang-format"
printf '%s\n' "$(basename "$quality_dep_build")" > "$runtime_dep/.last-build-dir"
printf '%s\n' "$(basename "$decoy_runtime_build")" > "$runtime_dep/.last-runtime-build-dir"
case "$(uname -s)" in
  Darwin) runtime_library_name="libp101_dep.dylib" ;;
  *) runtime_library_name="libp101_dep.so" ;;
esac
printf 'int dep_value(void)\n{\n    return 41;\n}\n' > "$runtime_dep/dep.c"
"$c_compiler" -shared -fPIC "$runtime_dep/dep.c" \
  -o "$quality_dep_build/$runtime_library_name"
printf 'int dep_value(void)\n{\n    return 0;\n}\n' > "$runtime_dep/dep.c"
"$c_compiler" -shared -fPIC "$runtime_dep/dep.c" \
  -o "$runtime_dep_build/$runtime_library_name"
printf 'int dep_value(void)\n{\n    return 42;\n}\n' > "$runtime_dep/dep.c"
"$c_compiler" -shared -fPIC "$runtime_dep/dep.c" \
  -o "$decoy_runtime_build/$runtime_library_name"
cat > "$PROJ/config.cmake" <<'EOF'
set(PROJECT_NAME runtime_link)
set(PROJECT_VERSION 1.0.0)
set(PROJECT_DESCRIPTION "runtime dependency precedence")
set(PROJECT_LANGUAGE C)
set(STANDARD_FLAGS -std=c17 -Werror)
set(EXECUTABLE_TARGETS hello)
set(hello_SOURCES src/main.c)
set(hello_LINK_LIBRARIES p101_dep)
EOF
printf 'int dep_value(void);\nint main(void)\n{\n    return dep_value();\n}\n' \
  > "$PROJ/src/main.c"
configure "$PROJ" -DP101_RUNTIME_ONLY=ON \
  -DP101_BUILD_LEVEL=1 \
  -DP101_BUILD_KEY="$runtime_build_key" -DSANITIZER_LIST=
if (( RC == 0 )); then
  build "$PROJ"
  if (( RC == 0 )) &&
     grep -q "$runtime_dep_build/$runtime_library_name" "$PROJ/configure.log" &&
     ! grep -q "$quality_dep_build/$runtime_library_name" "$PROJ/configure.log" &&
     ! grep -q "$decoy_runtime_build/$runtime_library_name" "$PROJ/configure.log" &&
     "$PROJ/build/hello"; then
    ok "runtime-link: exact lane wins at link and load time"
  elif (( RC == 0 )); then
    bad "runtime-link: strict sibling artifact won dependency resolution" \
      "$PROJ/configure.log"
  else
    bad "runtime-link: build failed" "$PROJ/build.log"
  fi
else
  bad "runtime-link: configure failed" "$PROJ/configure.log"
fi

# A formerly co-owned target must resolve from its extracted repository, even
# when a stale artifact with the same filename remains under the old owner.
record_dep="$runtime_root/libraries/lib_record"
legacy_record_owner="$runtime_root/libraries/lib_tool_event"
record_dep_build="$record_dep/build-${runtime_build_key}"
legacy_record_build="$legacy_record_owner/build-${runtime_build_key}"
mkdir -p "$record_dep_build" "$legacy_record_build"
case "$(uname -s)" in
  Darwin) record_library_name="libp101_record.dylib" ;;
  *) record_library_name="libp101_record.so" ;;
esac
printf 'int record_value(void)\n{\n    return 42;\n}\n' > "$record_dep/record.c"
"$c_compiler" -shared -fPIC "$record_dep/record.c" \
  -o "$record_dep_build/$record_library_name"
printf 'int record_value(void)\n{\n    return 0;\n}\n' > "$legacy_record_owner/record.c"
"$c_compiler" -shared -fPIC "$legacy_record_owner/record.c" \
  -o "$legacy_record_build/$record_library_name"
PROJ="$runtime_root/libraries/lib_record_consumer"
mkdir -p "$PROJ/src" "$PROJ/include" "$PROJ/.flags/$(basename "$c_compiler")"
cp "$CMAKE_FILE" "$PROJ/CMakeLists.txt"
ln -sfn "$(CDPATH='' cd "$(dirname "$CMAKE_FILE")" && pwd)/cmake" "$PROJ/cmake"
printf 'BasedOnStyle: LLVM\nIndentWidth: 4\nBreakBeforeBraces: Allman\nAllowShortFunctionsOnASingleLine: None\n' > "$PROJ/.clang-format"
cat > "$PROJ/config.cmake" <<'EOF'
set(PROJECT_NAME record_link)
set(PROJECT_VERSION 1.0.0)
set(PROJECT_DESCRIPTION "extracted record dependency ownership")
set(PROJECT_LANGUAGE C)
set(STANDARD_FLAGS -std=c17 -Werror)
set(EXECUTABLE_TARGETS hello)
set(hello_SOURCES src/main.c)
set(hello_LINK_LIBRARIES p101_record)
EOF
printf 'int record_value(void);\nint main(void)\n{\n    int value;\n\n    value = record_value();\n    return value == 42 ? 0 : 1;\n}\n' \
  > "$PROJ/src/main.c"
configure "$PROJ" -DP101_RUNTIME_ONLY=ON \
  -DP101_BUILD_LEVEL=1 \
  -DP101_BUILD_KEY="$runtime_build_key" -DSANITIZER_LIST=
if (( RC == 0 )); then
  build "$PROJ"
  if (( RC == 0 )) &&
     grep -q "$record_dep_build/$record_library_name" "$PROJ/configure.log" &&
     ! grep -q "$legacy_record_build/$record_library_name" "$PROJ/configure.log" &&
     "$PROJ/build/hello"; then
    ok "runtime-link: extracted p101_record owner wins over legacy artifact"
  elif (( RC == 0 )); then
    bad "runtime-link: p101_record resolved from its former owner" \
      "$PROJ/configure.log"
  else
    bad "runtime-link: extracted p101_record consumer failed to build" \
      "$PROJ/build.log"
  fi
else
  bad "runtime-link: extracted p101_record consumer failed to configure" \
    "$PROJ/configure.log"
fi

# A consumer of a public header needs the complete declared dependency include
# closure, even when it links only the outer library directly.
outer_dep="$runtime_root/libraries/lib_outer"
outer_dep_build="$outer_dep/build-${runtime_build_key}"
mkdir -p "$outer_dep/include/p101_outer" "$runtime_dep/include/p101_dep" \
  "$outer_dep_build"
cat > "$outer_dep/config.cmake" <<'EOF'
set(LIBRARY_TARGETS p101_outer)
set(p101_outer_LINK_LIBRARIES p101_dep)
EOF
cat > "$runtime_dep/config.cmake" <<'EOF'
set(LIBRARY_TARGETS p101_dep)
set(p101_dep_LINK_LIBRARIES "")
EOF
printf '#ifndef P101_DEP_DEP_H\n#define P101_DEP_DEP_H\n#define P101_DEP_VALUE 42\n#endif\n' \
  > "$runtime_dep/include/p101_dep/dep.h"
printf '#ifndef P101_OUTER_OUTER_H\n#define P101_OUTER_OUTER_H\n#include <p101_dep/dep.h>\n#endif\n' \
  > "$outer_dep/include/p101_outer/outer.h"
case "$(uname -s)" in
  Darwin) outer_library_name="libp101_outer.dylib" ;;
  *) outer_library_name="libp101_outer.so" ;;
esac
printf 'int outer_value(void)\n{\n    return 42;\n}\n' > "$outer_dep/outer.c"
"$c_compiler" -shared -fPIC "$outer_dep/outer.c" \
  -o "$outer_dep_build/$outer_library_name"
PROJ="$runtime_root/libraries/lib_outer_consumer"
mkdir -p "$PROJ/src" "$PROJ/include" "$PROJ/.flags/$(basename "$c_compiler")"
cp "$CMAKE_FILE" "$PROJ/CMakeLists.txt"
ln -sfn "$(CDPATH='' cd "$(dirname "$CMAKE_FILE")" && pwd)/cmake" "$PROJ/cmake"
printf 'BasedOnStyle: LLVM\nIndentWidth: 4\nBreakBeforeBraces: Allman\nAllowShortFunctionsOnASingleLine: None\n' > "$PROJ/.clang-format"
cat > "$PROJ/config.cmake" <<'EOF'
set(PROJECT_NAME outer_link)
set(PROJECT_VERSION 1.0.0)
set(PROJECT_DESCRIPTION "transitive public include closure")
set(PROJECT_LANGUAGE C)
set(STANDARD_FLAGS -std=c17 -Werror)
set(EXECUTABLE_TARGETS hello)
set(hello_SOURCES src/main.c)
set(hello_LINK_LIBRARIES p101_outer)
EOF
printf '#include <p101_outer/outer.h>\nint main(void)\n{\n    return P101_DEP_VALUE == 42 ? 0 : 1;\n}\n' \
  > "$PROJ/src/main.c"
configure "$PROJ" -DP101_RUNTIME_ONLY=ON \
  -DP101_BUILD_LEVEL=1 \
  -DP101_BUILD_KEY="$runtime_build_key" -DSANITIZER_LIST=
if (( RC == 0 )); then
  build "$PROJ"
  if (( RC == 0 )) &&
     grep -q "$outer_dep/include" "$PROJ/build/compile_commands.json" &&
     grep -q "$runtime_dep/include" "$PROJ/build/compile_commands.json" &&
     "$PROJ/build/hello"; then
    ok "include-closure: consumer receives transitive public dependency headers"
  elif (( RC == 0 )); then
    bad "include-closure: transitive dependency header was omitted" \
      "$PROJ/build/compile_commands.json"
  else
    bad "include-closure: consumer failed to build" "$PROJ/build.log"
  fi
else
  bad "include-closure: consumer failed to configure" "$PROJ/configure.log"
fi

# A repository may own multiple narrow public targets. Exact-lane resolution
# must use declared workspace ownership rather than synthesizing lib_<target>.
numeric_dep="$runtime_root/libraries/lib_numeric"
retired_random_owner="$runtime_root/libraries/lib_random"
numeric_dep_build="$numeric_dep/build-${runtime_build_key}"
retired_random_build="$retired_random_owner/build-${runtime_build_key}"
mkdir -p "$numeric_dep_build" "$retired_random_build"
case "$(uname -s)" in
  Darwin) random_library_name="libp101_random.dylib" ;;
  *) random_library_name="libp101_random.so" ;;
esac
printf 'int random_value(void)\n{\n    return 42;\n}\n' > "$numeric_dep/random.c"
"$c_compiler" -shared -fPIC "$numeric_dep/random.c" \
  -o "$numeric_dep_build/$random_library_name"
printf 'int random_value(void)\n{\n    return 0;\n}\n' > "$retired_random_owner/random.c"
"$c_compiler" -shared -fPIC "$retired_random_owner/random.c" \
  -o "$retired_random_build/$random_library_name"
PROJ="$runtime_root/libraries/lib_random_consumer"
mkdir -p "$PROJ/src" "$PROJ/include" "$PROJ/.flags/$(basename "$c_compiler")"
cp "$CMAKE_FILE" "$PROJ/CMakeLists.txt"
ln -sfn "$(CDPATH='' cd "$(dirname "$CMAKE_FILE")" && pwd)/cmake" "$PROJ/cmake"
printf 'BasedOnStyle: LLVM\nIndentWidth: 4\nBreakBeforeBraces: Allman\nAllowShortFunctionsOnASingleLine: None\n' > "$PROJ/.clang-format"
cat > "$PROJ/config.cmake" <<'EOF'
set(PROJECT_NAME random_link)
set(PROJECT_VERSION 1.0.0)
set(PROJECT_DESCRIPTION "combined repository dependency ownership")
set(PROJECT_LANGUAGE C)
set(STANDARD_FLAGS -std=c17 -Werror)
set(EXECUTABLE_TARGETS hello)
set(hello_SOURCES src/main.c)
set(hello_LINK_LIBRARIES p101_random)
EOF
printf 'int random_value(void);\nint main(void)\n{\n    int value;\n\n    value = random_value();\n    return value == 42 ? 0 : 1;\n}\n' \
  > "$PROJ/src/main.c"
configure "$PROJ" -DP101_RUNTIME_ONLY=ON \
  -DP101_BUILD_LEVEL=1 \
  -DP101_BUILD_KEY="$runtime_build_key" -DSANITIZER_LIST=
if (( RC == 0 )); then
  build "$PROJ"
  if (( RC == 0 )) &&
     grep -q "$numeric_dep_build/$random_library_name" "$PROJ/configure.log" &&
     ! grep -q "$retired_random_build/$random_library_name" "$PROJ/configure.log" &&
     "$PROJ/build/hello"; then
    ok "runtime-link: combined p101_random owner resolves from lib_numeric"
  elif (( RC == 0 )); then
    bad "runtime-link: p101_random resolved from its retired repository" \
      "$PROJ/configure.log"
  else
    bad "runtime-link: combined p101_random consumer failed to build" \
      "$PROJ/build.log"
  fi
else
  bad "runtime-link: combined p101_random consumer failed to configure" \
    "$PROJ/configure.log"
fi

# An explicit matrix identity is a hard isolation boundary. If its sibling
# artifact is absent, configuration must fail instead of silently consuming a
# stale marker or a different compiler's build directory.
PROJ="$runtime_root/libraries/lib_missing_consumer"
mkdir -p "$PROJ/src" "$PROJ/include" "$PROJ/.flags/$(basename "$c_compiler")"
cp "$CMAKE_FILE" "$PROJ/CMakeLists.txt"
cp "$runtime_root/libraries/lib_consumer/config.cmake" "$PROJ/config.cmake"
cp "$runtime_root/libraries/lib_consumer/.clang-format" "$PROJ/.clang-format"
cp "$runtime_root/libraries/lib_consumer/src/main.c" "$PROJ/src/main.c"
ln -sfn "$(CDPATH='' cd "$(dirname "$CMAKE_FILE")" && pwd)/cmake" "$PROJ/cmake"
configure "$PROJ" -DP101_RUNTIME_ONLY=ON \
  -DP101_BUILD_LEVEL=1 \
  -DP101_BUILD_KEY=missing-matrix-alias -DSANITIZER_LIST=
if (( RC == 0 )); then
  build "$PROJ"
fi
if (( RC != 0 )) &&
   ! grep -q "$quality_dep_build/$runtime_library_name" "$PROJ/configure.log" &&
   ! grep -q "$decoy_runtime_build/$runtime_library_name" "$PROJ/configure.log"; then
  ok "runtime-link: missing keyed artifact rejects stale fallback at link"
else
  bad "runtime-link: missing keyed artifact consumed stale fallback" \
    "$PROJ/build.log"
fi

# ---------- case: macOS ASan must load before user dylibs ----------
if [[ "$(uname -s)" == "Darwin" ]] &&
   "$c_compiler" --version 2>/dev/null | grep -qi clang &&
   printf 'int main(void){return 0;}\n' |
     "$c_compiler" -x c - -fsanitize=address \
       -o "$SANDBOX/asan-probe" >/dev/null 2>&1 &&
   run_with_timeout 5 "$SANDBOX/asan-probe" >/dev/null 2>&1; then
  new_proj macos-asan-order
  cat > "$PROJ/config.cmake" <<'EOF'
set(PROJECT_NAME sample)
set(PROJECT_VERSION 1.0.0)
set(PROJECT_DESCRIPTION "macOS ASan load-order sample")
set(PROJECT_LANGUAGE C)
set(STANDARD_FLAGS -std=c17 -Werror)
set(LIBRARY_TARGETS sample_runtime)
set(EXECUTABLE_TARGETS hello)
set(sample_runtime_SOURCES src/runtime.c)
set(sample_runtime_HEADERS include/runtime.h)
set(sample_runtime_LINK_LIBRARIES "")
set(hello_SOURCES src/main.c)
set(hello_LINK_LIBRARIES sample_runtime)
EOF
  printf '#ifndef RUNTIME_H\n#define RUNTIME_H\nint runtime_value(void);\n#endif\n' \
    > "$PROJ/include/runtime.h"
  printf '#include "runtime.h"\nint runtime_value(void)\n{\n    return 0;\n}\n' \
    > "$PROJ/src/runtime.c"
  printf '#include "runtime.h"\nint main(void)\n{\n    return runtime_value();\n}\n' \
    > "$PROJ/src/main.c"
  printf '%s\n' '-fsanitize=address' \
    > "$PROJ/.flags/$(basename "$c_compiler")/address_sanitizer_flags.txt"
  configure "$PROJ" -DSANITIZER_LIST=address
  if (( RC == 0 )); then
    build "$PROJ"
    asan_first_dependency="$(
      otool -L "$PROJ/build/hello" 2>/dev/null | sed -n '2p'
    )"
    if (( RC == 0 )) &&
       [[ "$asan_first_dependency" == *libclang_rt.asan_osx_dynamic.dylib* ]] &&
       run_with_timeout 5 "$PROJ/build/hello" >/dev/null 2>&1; then
      ok "macos-asan-order: compiler ASan runtime precedes user dylibs"
    elif (( RC == 0 )); then
      bad "macos-asan-order: ASan is not the first Mach-O dependency" \
        "$PROJ/build.log"
    else
      bad "macos-asan-order: build failed" "$PROJ/build.log"
    fi
  else
    bad "macos-asan-order: configure failed" "$PROJ/configure.log"
  fi
else
  echo "note: macOS Clang ASan unavailable at compile or runtime; skipping dylib load-order case."
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

# ---------- case: requested sanitizer must exist ----------
# An explicitly requested sanitizer must never disappear silently. The
# bring-up escape hatch is deliberately opt-in and visibly warns.
new_proj missing-sanitizer
write_c_config_exe "$PROJ"
printf 'int main(void)\n{\n    return 0;\n}\n' > "$PROJ/src/main.c"
configure "$PROJ" -DSANITIZER_LIST=p101_missing
if (( RC != 0 )) && grep -q "Requested sanitizer 'p101_missing'" "$PROJ/configure.log"; then
  ok "missing-sanitizer: requested unavailable sanitizer fails configure"
else
  bad "missing-sanitizer: requested unavailable sanitizer was silently ignored" "$PROJ/configure.log"
fi

rm -rf "$PROJ/build"
configure "$PROJ" -DSANITIZER_LIST=p101_missing -DP101_ALLOW_MISSING_SANITIZERS=ON
if (( RC == 0 )) && grep -q "P101_ALLOW_MISSING_SANITIZERS is ON" "$PROJ/configure.log"; then
  ok "missing-sanitizer(opt-out): explicit bring-up flag permits configure"
else
  bad "missing-sanitizer(opt-out): explicit bring-up flag did not permit configure" "$PROJ/configure.log"
fi

# ---------- case: flag-cache-refresh ----------
new_proj flag-cache-refresh
write_c_config_exe "$PROJ"
printf 'int main(void)\n{\n    return 0;\n}\n' > "$PROJ/src/main.c"
flag_cache="$PROJ/.flags/$(basename "$c_compiler")/code_generation_flags.txt"
printf '%s' "-DP101_CACHE_SENTINEL=1" > "$flag_cache"
configure "$PROJ"
if (( RC == 0 )); then
  printf '%s' "-DP101_CACHE_SENTINEL=2" > "$flag_cache"
  build "$PROJ"
  if (( RC == 0 )) &&
     grep -q 'Extra CFLAGS:.*P101_CACHE_SENTINEL=2' "$PROJ/build.log" &&
     grep -q -- '-DP101_CACHE_SENTINEL=2' "$PROJ/build/compile_commands.json"; then
    ok "flag-cache-refresh: cache edit automatically reconfigures the build"
  else
    bad "flag-cache-refresh: build retained stale probed flags" "$PROJ/build.log"
  fi
else
  bad "flag-cache-refresh: configure failed" "$PROJ/configure.log"
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

# ---------- case: format-check ----------
# A normal build checks formatting but must not rewrite tracked source. The
# explicit format target owns mutation.
if have clang-format; then
  new_proj format-check
  write_c_config_exe "$PROJ"
  printf 'int main(void){return 0;}\n' > "$PROJ/src/main.c"
  format_before="$(cksum "$PROJ/src/main.c")"
  configure "$PROJ"
  if (( RC == 0 )); then
    build "$PROJ"
    format_after="$(cksum "$PROJ/src/main.c")"
    if (( RC != 0 )) && [[ "$format_before" == "$format_after" ]] \
       && grep -qi 'clang-format' "$PROJ/build.log"; then
      if MAKEFLAGS='' MFLAGS='' MAKELEVEL=0 \
           cmake --build "$PROJ/build" --target sample_format_all \
           > "$PROJ/format.log" 2>&1; then
        build "$PROJ"
        if (( RC == 0 )); then
          ok "format-check: default build is check-only; explicit target formats"
        else
          bad "format-check: formatted source did not pass the build" "$PROJ/build.log"
        fi
      else
        bad "format-check: explicit format target failed" "$PROJ/format.log"
      fi
    elif (( RC == 0 )); then
      bad "format-check: unformatted source passed the default build" "$PROJ/build.log"
    else
      bad "format-check: build rewrote source or failed for another reason" "$PROJ/build.log"
    fi
  else
    bad "format-check: configure failed" "$PROJ/configure.log"
  fi
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
if have "$c_compiler" &&
   "$c_compiler" --version 2>/dev/null | head -1 | grep -qi clang; then
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
if [[ "$(uname -s)" == Linux ]] && [[ "$c_compiler" == *gcc* ]] && have "$c_compiler" \
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
  printf '%s' "$_sjsrc" > "$PROJ/src/main.c"
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
  printf '%s' "$_sjsrc" > "$PROJ/src/main.c"
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
  echo "note: no Linux gcc with enforced -fharden-control-flow-redundancy; skipping file-flag-optout case."
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

profile_supported=false
if printf 'int main(void){return 0;}\n' |
   "$c_compiler" -x c - -pg -o "$SANDBOX/profile-probe" >/dev/null 2>&1
then
  profile_supported=true
fi

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
if $profile_supported; then
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
else
  echo "note: compiler cannot link a -pg executable; skipping profile case."
fi

# ---------- case: nested workspace declared local-header precedence ----------
# Playground tracks are one level deeper than libraries/programs. Their local
# checked-out, declared dependency headers must use -I and win over stale
# installed headers. Unrelated workspace libraries must not be exposed.
nested_root="$SANDBOX/nested-workspace"
PROJ="$nested_root/playgrounds/tracks/sample"
mkdir -p "$PROJ/src" "$PROJ/include" "$PROJ/.flags/$(basename "$c_compiler")"
mkdir -p "$nested_root/libraries/lib_fixture/include"
mkdir -p "$nested_root/libraries/lib_record/include"
mkdir -p "$nested_root/libraries/lib_unrelated/include"
cp "$CMAKE_FILE" "$PROJ/CMakeLists.txt"
ln -sfn "$(CDPATH='' cd "$(dirname "$CMAKE_FILE")" && pwd)/cmake" "$PROJ/cmake"
printf 'BasedOnStyle: LLVM\nIndentWidth: 4\nBreakBeforeBraces: Allman\nAllowShortFunctionsOnASingleLine: None\n' > "$PROJ/.clang-format"
cat > "$PROJ/config.cmake" <<'EOF'
set(PROJECT_NAME nested)
set(PROJECT_VERSION 1.0.0)
set(PROJECT_DESCRIPTION "nested workspace precedence")
set(PROJECT_LANGUAGE C)
set(STANDARD_FLAGS -std=c17 -Werror)
set(LIBRARY_TARGETS p101_fixture p101_record)
set(EXECUTABLE_TARGETS hello)
set(p101_fixture_SOURCES src/fixture.c)
set(p101_record_SOURCES src/record.c)
set(hello_SOURCES src/main.c)
set(hello_LINK_LIBRARIES p101_fixture p101_record)
EOF
printf '#include <local_only.h>\nint fixture_anchor(void)\n{\n    return 0;\n}\n' > "$PROJ/src/fixture.c"
printf '#include "record_decl.h"\nint record_anchor(void)\n{\n    return 0;\n}\n' > "$PROJ/src/record.c"
printf '#ifndef RECORD_DECL_H\n#define RECORD_DECL_H\nint record_anchor(void);\n#endif\n' > "$PROJ/include/record_decl.h"
printf '#ifndef LOCAL_ONLY_H\n#define LOCAL_ONLY_H\n#define LOCAL_VALUE 0\nint fixture_anchor(void);\n#endif\n' > "$nested_root/libraries/lib_fixture/include/local_only.h"
printf '#ifndef RECORD_ONLY_H\n#define RECORD_ONLY_H\nint record_anchor(void);\n#endif\n' > "$nested_root/libraries/lib_record/include/record_only.h"
printf '#ifndef UNRELATED_H\n#define UNRELATED_H\n#endif\n' > "$nested_root/libraries/lib_unrelated/include/unrelated.h"
printf '#include <local_only.h>\n#include <record_only.h>\nint main(void)\n{\n    return LOCAL_VALUE + fixture_anchor() + record_anchor();\n}\n' > "$PROJ/src/main.c"
configure "$PROJ"
if (( RC == 0 )); then
  build "$PROJ"
  nested_compile_db="$PROJ/build/compile_commands.json"
  if (( RC == 0 )) && [ -f "$nested_compile_db" ] \
     && grep -q -- "-I$nested_root/libraries/lib_fixture/include" "$nested_compile_db" \
     && grep -q -- "-I$nested_root/libraries/lib_record/include" "$nested_compile_db" \
     && ! grep -q -- "-isystem $nested_root/libraries/lib_fixture/include" "$nested_compile_db" \
     && ! grep -q -- "$nested_root/libraries/lib_unrelated/include" "$nested_compile_db"; then
    ok "nested-local-precedence: declared workspace headers use -I; unrelated roots stay hidden"
  elif (( RC == 0 )); then
    bad "nested-local-precedence: local header did not use normal -I precedence" "$nested_compile_db"
  else
    bad "nested-local-precedence: build failed" "$PROJ/build.log"
  fi
else
  bad "nested-local-precedence: configure failed" "$PROJ/configure.log"
fi

# ---------- sanitized compile DB keeps semantics, drops foreign codegen ----------
compile_db_helper="$SANDBOX/p101-compile-db"
"$c_compiler" -std=c17 -Wall -Wextra -Werror -pedantic \
    "$SCRIPT_DIR/cmake/p101_compile_db.c" \
  -o "$compile_db_helper"
sanitize_input="$SANDBOX/sanitize-input.json"
sanitize_output="$SANDBOX/sanitize-output.json"
cat > "$sanitize_input" <<'EOF'
[
  {
    "directory": "/tmp",
    "arguments": [
      "gcc",
      "-fno-var-tracking-assignments",
      "-ffat-lto-objects",
      "-fwrapv",
      "-fno-strict-aliasing",
      "-fshort-wchar",
      "sample.c"
    ],
    "file": "sample.c"
  }
]
EOF
"$compile_db_helper" sanitize \
  "$sanitize_input" "$sanitize_output"
if grep -q -- '"-fwrapv"' "$sanitize_output" \
   && grep -q -- '"-fno-strict-aliasing"' "$sanitize_output" \
   && grep -q -- '"-fshort-wchar"' "$sanitize_output" \
   && ! grep -q -- 'var-tracking' "$sanitize_output" \
   && ! grep -q -- 'fat-lto' "$sanitize_output"; then
  ok "tidy-db-flags: semantic flags kept and GCC-only codegen flags removed"
else
  bad "tidy-db-flags: sanitized compile DB changed the admitted flag policy" "$sanitize_output"
fi

# ---------- CTU distinguishes header-only from filtered source evidence ----------
ctu_root="$SANDBOX/ctu-root"
ctu_work="$SANDBOX/ctu-work"
mkdir -p "$ctu_root" "$ctu_work"
printf '[]\n' > "$SANDBOX/ctu-empty.json"
if "$compile_db_helper" ctu \
     /bin/false /bin/false "$SANDBOX/ctu-empty.json" "$ctu_work" 1 "$ctu_root" \
     -- > "$SANDBOX/ctu-empty.log" 2>&1; then
  ok "ctu-empty: header-only compile database is a clean optional skip"
else
  bad "ctu-empty: header-only compile database was treated as broken" "$SANDBOX/ctu-empty.log"
fi
cat > "$SANDBOX/ctu-filtered.json" <<'EOF'
[
  {
    "directory": "/tmp",
    "arguments": ["cc", "-c", "/tmp/outside.c"],
    "file": "/tmp/outside.c"
  }
]
EOF
ctu_filtered_rc=0
"$compile_db_helper" ctu \
  /bin/false /bin/false "$SANDBOX/ctu-filtered.json" "$ctu_work" 1 "$ctu_root" \
  -- > "$SANDBOX/ctu-filtered.log" 2>&1 || ctu_filtered_rc=$?
if [[ "$ctu_filtered_rc" -eq 2 ]] \
   && grep -q 'no in-tree translation units' "$SANDBOX/ctu-filtered.log"; then
  ok "ctu-filtered: source evidence filtered out of tree is rejected"
else
  bad "ctu-filtered: misconfigured source database was not rejected" "$SANDBOX/ctu-filtered.log"
fi

# ---------- summary ----------
minimum_passes=15
if (( pass < minimum_passes )); then
  bad "harness-floor: only $pass cases ran; expected at least $minimum_passes"
fi
echo
echo "== test-cmake.sh: $pass passed, $fail failed =="
if (( fail > 0 )); then
  $keep || echo "(re-run with -k to keep the sandbox and inspect logs)"
  exit 1
fi
exit 0
