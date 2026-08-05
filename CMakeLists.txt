cmake_minimum_required(VERSION 3.20)

# =========================
# Project config (external; no fallbacks)
# =========================
include("${CMAKE_CURRENT_SOURCE_DIR}/config.cmake" OPTIONAL RESULT_VARIABLE _HAVE_CFG)
if (NOT _HAVE_CFG)
    message(FATAL_ERROR "config.cmake not found next to CMakeLists.txt")
endif ()

foreach (_req IN ITEMS
        PROJECT_NAME PROJECT_VERSION PROJECT_DESCRIPTION PROJECT_LANGUAGE STANDARD_FLAGS
)
    if (NOT DEFINED ${_req})
        message(FATAL_ERROR "config.cmake must define ${_req}")
    endif ()
    if ("${${_req}}" STREQUAL "")
        message(FATAL_ERROR "config.cmake variable ${_req} must be non-empty")
    endif ()
endforeach ()

if (NOT PROJECT_LANGUAGE MATCHES "^(C|CXX)$")
    message(FATAL_ERROR "PROJECT_LANGUAGE must be 'C' or 'CXX'")
endif ()

# Some non-system Clang packages ship a default driver configuration whose
# SDK path no longer exists after an OS/Xcode update. Select the active SDK
# before project() so CMake passes an explicit, valid sysroot during compiler
# identification and every subsequent compile/link operation.
set(_P101_SELECT_HOST_MACOS_SYSROOT FALSE)
if (CMAKE_HOST_APPLE)
    if (NOT CMAKE_OSX_SYSROOT)
        set(_P101_SELECT_HOST_MACOS_SYSROOT TRUE)
    elseif (IS_ABSOLUTE "${CMAKE_OSX_SYSROOT}"
            AND NOT IS_DIRECTORY "${CMAKE_OSX_SYSROOT}")
        set(_P101_SELECT_HOST_MACOS_SYSROOT TRUE)
    endif ()
endif ()
if (_P101_SELECT_HOST_MACOS_SYSROOT)
    execute_process(
            COMMAND xcrun --sdk macosx --show-sdk-path
            OUTPUT_VARIABLE _P101_HOST_MACOS_SYSROOT
            OUTPUT_STRIP_TRAILING_WHITESPACE
            ERROR_QUIET)
    if (IS_DIRECTORY "${_P101_HOST_MACOS_SYSROOT}")
        set(CMAKE_OSX_SYSROOT "${_P101_HOST_MACOS_SYSROOT}" CACHE PATH
                "Active macOS SDK selected by xcrun" FORCE)
    endif ()
endif ()

project(${PROJECT_NAME}
        VERSION ${PROJECT_VERSION}
        DESCRIPTION "${PROJECT_DESCRIPTION}"
        LANGUAGES ${PROJECT_LANGUAGE})

# A repository quality build deliberately runs every configured analyzer and
# may link compiler-private sanitizer runtimes.  Installed p101 libraries are a
# consumer boundary: they must not force one compiler's sanitizer runtime into
# a program built by another compiler.  The workspace build driver therefore
# creates a second, sanitizer-free build with this option enabled and installs
# from it.  Keep the primary targets and install rules, but omit the expensive
# quality pipeline from that runtime build.
option(P101_RUNTIME_ONLY
        "Build installable runtime artifacts without the analyzer pipeline"
        OFF)

# config.cmake is read before project() so it can define PROJECT_NAME and
# PROJECT_LANGUAGE. Platform and compiler conditionals such as
# CMAKE_SYSTEM_NAME, APPLE, UNIX, and CMAKE_*_COMPILER_ID are only reliable
# after project(), so repos may add a config-post.cmake hook for those.
include("${CMAKE_CURRENT_SOURCE_DIR}/config-post.cmake" OPTIONAL)

# Derive helpers for language/compilers
if (PROJECT_LANGUAGE STREQUAL "CXX")
    set(_LANG CXX)
    set(_COMPILER_ID "${CMAKE_CXX_COMPILER_ID}")
    set(_COMPILER "${CMAKE_CXX_COMPILER}")
else ()
    set(_LANG C)
    set(_COMPILER_ID "${CMAKE_C_COMPILER_ID}")
    set(_COMPILER "${CMAKE_C_COMPILER}")
endif ()

# Single source of truth for the language standard: the -std= entry in
# STANDARD_FLAGS. cppcheck and the Clang Static Analyzer derive from this
# too, so they can never analyze under a different standard than the one
# the compiler builds with.
set(P101_STD_FLAG "")
foreach (_sf IN LISTS STANDARD_FLAGS)
    if (_sf MATCHES "^-std=")
        set(P101_STD_FLAG "${_sf}")
    endif ()
endforeach ()

set(P101_EFFECTIVE_STANDARD_FLAGS ${STANDARD_FLAGS})
if (APPLE AND DEFINED DARWIN_STANDARD_FLAGS)
    list(APPEND P101_EFFECTIVE_STANDARD_FLAGS ${DARWIN_STANDARD_FLAGS})
elseif (CMAKE_SYSTEM_NAME STREQUAL "Linux" AND DEFINED LINUX_STANDARD_FLAGS)
    list(APPEND P101_EFFECTIVE_STANDARD_FLAGS ${LINUX_STANDARD_FLAGS})
elseif (CMAKE_SYSTEM_NAME MATCHES "FreeBSD|DragonFly|OpenBSD|NetBSD" AND DEFINED BSD_STANDARD_FLAGS)
    list(APPEND P101_EFFECTIVE_STANDARD_FLAGS ${BSD_STANDARD_FLAGS})
endif ()

set(P101_FEATURE_TEST_FLAGS "")
foreach (_sf IN LISTS P101_EFFECTIVE_STANDARD_FLAGS)
    if (_sf MATCHES "^-D")
        list(APPEND P101_FEATURE_TEST_FLAGS "${_sf}")
    endif ()
endforeach ()

# =========================
# Tool paths (required/optional)
# =========================
set(ICON "🔧" CACHE STRING "Icon prefix")

# Required: clang-tidy
find_program(CLANG_TIDY_NAME NAMES clang-tidy REQUIRED DOC "clang-tidy executable used by the build")

# Optional: clang-format and scan-build
find_program(CLANG_FORMAT_NAME NAMES clang-format DOC "clang-format executable used by the build")
find_program(SCAN_BUILD_NAME NAMES scan-build DOC "scan-build executable used by the build")

# Enforce: cppcheck must exist and will always run
find_program(CPPCHECK_NAME NAMES cppcheck REQUIRED DOC "cppcheck executable used by the build")
execute_process(
        COMMAND "${CPPCHECK_NAME}" --version
        OUTPUT_VARIABLE _P101_CPPCHECK_VERSION_TEXT
        ERROR_VARIABLE _P101_CPPCHECK_VERSION_ERROR
        OUTPUT_STRIP_TRAILING_WHITESPACE
        ERROR_STRIP_TRAILING_WHITESPACE
        RESULT_VARIABLE _P101_CPPCHECK_VERSION_RC)
if (_P101_CPPCHECK_VERSION_RC EQUAL 0)
    string(REGEX MATCH "[0-9]+\\.[0-9]+(\\.[0-9]+)?" _P101_CPPCHECK_VERSION "${_P101_CPPCHECK_VERSION_TEXT}")
    if (_P101_CPPCHECK_VERSION VERSION_LESS "2.14")
        message(WARNING "cppcheck ${_P101_CPPCHECK_VERSION} is older than the p101 known-good floor 2.14; upgrade cppcheck if older-version self-diagnostics appear.")
    endif ()
else ()
    message(WARNING "Could not determine cppcheck version: ${_P101_CPPCHECK_VERSION_ERROR}")
endif ()

# Python (required for JSON-safe compile DB sanitizer + analyze-from-DB)
find_program(PYTHON3_EXECUTABLE NAMES python3 python REQUIRED DOC "Python executable for helper scripts")

# Sanitizers requested (comma-separated names) -> read matching *_sanitizer_flags.txt
set(SANITIZER_LIST "" CACHE STRING "Comma-separated sanitizers to try to enable")
set(P101_ALLOW_MISSING_SANITIZERS OFF CACHE BOOL
        "Permit explicitly requested sanitizers to be unavailable (bring-up only)")

# Extra public include/link roots
set(P101_PUBLIC_INCLUDE_DIRS "/usr/local/include" CACHE STRING "Extra public include dirs")
set(P101_PUBLIC_LINK_DIRS "/usr/local/lib;/usr/local/lib64" CACHE STRING "Extra link dirs")
if (NOT DEFINED P101_PUBLIC_INCLUDE_DIRS OR P101_PUBLIC_INCLUDE_DIRS STREQUAL "")
    set(P101_PUBLIC_INCLUDE_DIRS "/usr/local/include" CACHE STRING "Extra public include dirs" FORCE)
endif ()
if (NOT DEFINED P101_PUBLIC_LINK_DIRS OR P101_PUBLIC_LINK_DIRS STREQUAL "")
    set(P101_PUBLIC_LINK_DIRS "/usr/local/lib;/usr/local/lib64" CACHE STRING "Extra link dirs" FORCE)
endif ()

# Analyzer knobs
set(P101_TIDY_CHECKS "" CACHE STRING "clang-tidy --checks= (empty -> sane default)")
set(P101_TIDY_EXTRA_ARGS "" CACHE STRING "Extra clang-tidy args (space-separated)")
set(P101_CPPCHECK_ARGS "" CACHE STRING "cppcheck args (space-separated)")
set(P101_HEADER_FILTER ".*" CACHE STRING "Regex for -header-filter (default: all)")

set(P101_ANALYZE_FAIL_ON_DIAGNOSTICS OFF CACHE BOOL "Fail build if analyze stage emits diagnostics")

# Clang Static Analyzer deep pass
set(P101_CLANG_SA_PROFILE "${P101_CLANG_SA_PROFILE}" CACHE STRING "CSA profile: basic|deep")
set_property(CACHE P101_CLANG_SA_PROFILE PROPERTY STRINGS basic deep)
set(P101_CLANG_SA_FAIL_ON_DIAGNOSTICS ON CACHE BOOL "Fail build on any CSA diagnostic")
set(P101_CLANG_SA_EXTRA_ARGS "" CACHE STRING "Extra --analyze args for clang (space-separated)")

# =========================
# QoL
# =========================
set(CMAKE_EXPORT_COMPILE_COMMANDS ON CACHE BOOL "Export compile_commands.json" FORCE)

# Surface compile_commands.json at the repo root so clangd / IDEs find it with
# no configuration. Points at whichever build dir was configured most recently;
# overwriting is harmless. (compile_commands.json is already in .gitignore.)
if (CMAKE_EXPORT_COMPILE_COMMANDS)
    file(CREATE_LINK
            "${CMAKE_BINARY_DIR}/compile_commands.json"
            "${CMAKE_SOURCE_DIR}/compile_commands.json"
            SYMBOLIC)
endif ()

include(GNUInstallDirs)

# -------------------------------------------------------------------------
# Shared CMake/Python helper scripts live in cmake/ (extracted from this
# file so they can be read, linted, and tested on their own). In the
# workspace each repo's cmake/ is a symlink to scripts/cmake/ created by
# link-cmake.sh, so editing a helper once updates every repo at once.
# -------------------------------------------------------------------------
set(P101_CMAKE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/cmake" CACHE PATH
        "Directory of the shared p101 CMake/Python helper scripts")
if (NOT EXISTS "${P101_CMAKE_DIR}/FailIfCppcheckDiagnostics.cmake")
    message(FATAL_ERROR
            "p101 helper scripts not found in ${P101_CMAKE_DIR}. In the "
            "workspace run scripts/distribution/link-cmake.sh to symlink them; for a "
            "standalone checkout run scripts/distribution/recreate-cmake-helpers.sh.")
endif ()
if (NOT EXISTS "${P101_CMAKE_DIR}/ToolConfig.cmake")
    message(FATAL_ERROR
            "p101 tool config not found in ${P101_CMAKE_DIR}. In the "
            "workspace run scripts/distribution/link-cmake.sh to symlink it; for a "
            "standalone checkout run scripts/distribution/recreate-cmake-helpers.sh.")
endif ()
include("${P101_CMAKE_DIR}/ToolConfig.cmake")

# Do not use CMake’s built-in tidy/cppcheck launchers
set(CMAKE_C_CLANG_TIDY "" CACHE STRING "" FORCE)
set(CMAKE_CXX_CLANG_TIDY "" CACHE STRING "" FORCE)
set(CMAKE_C_CPPCHECK "" CACHE STRING "" FORCE)
set(CMAKE_CXX_CPPCHECK "" CACHE STRING "" FORCE)

# Echo compile/link commands
set(_LAUNCH_CMD "test -n \"$P101_QUIET\" || echo \"${ICON} compiling/linking: $*\" && exec \"$@\"")
if (PROJECT_LANGUAGE STREQUAL "CXX")
    set(CMAKE_CXX_COMPILER_LAUNCHER "/bin/sh" "-c" "${_LAUNCH_CMD}" "cmake-launch")
else ()
    set(CMAKE_C_COMPILER_LAUNCHER "/bin/sh" "-c" "${_LAUNCH_CMD}" "cmake-launch")
endif ()

# Split cache strings to lists
separate_arguments(P101_PUBLIC_INCLUDE_DIRS_LIST NATIVE_COMMAND "${P101_PUBLIC_INCLUDE_DIRS}")
separate_arguments(P101_PUBLIC_LINK_DIRS_LIST NATIVE_COMMAND "${P101_PUBLIC_LINK_DIRS}")
if (P101_TIDY_EXTRA_CHECKS)
    set(P101_TIDY_EXTRA_CHECKS_LIST ${P101_TIDY_EXTRA_CHECKS})
endif ()
separate_arguments(P101_TIDY_EXTRA_ARGS_LIST NATIVE_COMMAND "${P101_TIDY_EXTRA_ARGS}")
separate_arguments(P101_CPPCHECK_ARGS_LIST NATIVE_COMMAND "${P101_CPPCHECK_ARGS}")
set(P101_CPPCHECK_SUPPRESSIONS_LIST ${P101_CPPCHECK_SUPPRESSIONS})
separate_arguments(P101_CLANG_SA_EXTRA_ARGS_LIST NATIVE_COMMAND "${P101_CLANG_SA_EXTRA_ARGS}")

# macOS convenience roots
if (APPLE)
    list(APPEND P101_PUBLIC_INCLUDE_DIRS_LIST /opt/homebrew/include /opt/local/include)
    list(APPEND P101_PUBLIC_LINK_DIRS_LIST /opt/homebrew/lib /opt/local/lib)
endif ()

# Filter helper
function(_p101_filter_existing_dirs OUT)
    set(_out "")
    foreach (_d IN LISTS ARGN)
        if (IS_DIRECTORY "${_d}")
            list(APPEND _out "${_d}")
        else ()
            message(STATUS "Skipping missing include/link dir: ${_d}")
        endif ()
    endforeach ()
    set(${OUT} "${_out}" PARENT_SCOPE)
endfunction()

# macOS SDK path
set(MAC_SYSROOT "${CMAKE_OSX_SYSROOT}")
if (APPLE AND NOT MAC_SYSROOT)
    execute_process(COMMAND xcrun --show-sdk-path
            OUTPUT_VARIABLE MAC_SYSROOT
            OUTPUT_STRIP_TRAILING_WHITESPACE
            ERROR_QUIET)
endif ()

# =========================
# Flags folder selection
# =========================
get_filename_component(_CC_BASE "${_COMPILER}" NAME) # e.g. gcc-15 or clang
# Profile-aware cache root: the 'standard' tier (P101_FLAGS_PROFILE=standard
# in the environment, set by update.sh --standard) reads .flags-standard/;
# the default/maximal tier reads .flags/. P101_NO_FLAGS below overrides both.
if ("$ENV{P101_FLAGS_PROFILE}" STREQUAL "standard")
    set(_FLAGS_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.flags-standard")
else ()
    set(_FLAGS_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.flags")
endif ()

set(_FLAG_DIRS_USED "")
if (EXISTS "${_FLAGS_ROOT}/${_CC_BASE}")
    set(_FLAG_DIRS_USED "${_FLAGS_ROOT}/${_CC_BASE}")
else ()
    if (DEFINED ENV{P101_ALLOW_NO_FLAGS}
            AND NOT "$ENV{P101_ALLOW_NO_FLAGS}" STREQUAL ""
            AND NOT "$ENV{P101_ALLOW_NO_FLAGS}" STREQUAL "0")
        message(WARNING "No flags dir for ${_CC_BASE}; P101_ALLOW_NO_FLAGS is set, so no compiler flags will be loaded.")
    else ()
        message(FATAL_ERROR
                "No flags dir for ${_CC_BASE} under ${_FLAGS_ROOT}. "
                "Run scripts/workspace/update.sh or scripts/generators/generate-flags.sh for this compiler, "
                "or set P101_ALLOW_NO_FLAGS=1 for an explicit flagless bring-up build.")
    endif ()
endif ()

# One-off flagless build: P101_NO_FLAGS in the ENVIRONMENT (set by
# update.sh / update-all.sh --no-flags) suppresses every probed compiler
# flag AND every sanitizer for this configure, WITHOUT touching the .flags
# caches or flag-selection.json. The env var propagates down the whole
# build chain, so no per-repo edit is needed. Unset (or 0/empty) -> normal.
# A plain set() here shadows the SANITIZER_LIST cache entry for the rest of
# this configure only; it does not persist into CMakeCache.txt.
if (DEFINED ENV{P101_NO_FLAGS}
        AND NOT "$ENV{P101_NO_FLAGS}" STREQUAL "0"
        AND NOT "$ENV{P101_NO_FLAGS}" STREQUAL "")
    set(_FLAG_DIRS_USED "")
    set(SANITIZER_LIST "")
    message(STATUS "P101_NO_FLAGS set: NO probed compiler flags or sanitizers this build.")
endif ()
message(STATUS "Flag dirs used:    ${_FLAG_DIRS_USED}")

# Flag probes are generated outside CMake. Treat every cache record and its
# compiler fingerprint as configure inputs so `cmake --build` automatically
# reconfigures after a toolchain change or a repaired probe. Without this,
# an interactive build retry can retain unsupported flags in its generated
# build rules even after the shared cache has been corrected.
if (_FLAG_DIRS_USED)
    file(GLOB _P101_FLAG_CACHE_INPUTS "${_FLAG_DIRS_USED}/*.txt")
    if (EXISTS "${_FLAG_DIRS_USED}/.compiler-fingerprint")
        list(APPEND _P101_FLAG_CACHE_INPUTS
                "${_FLAG_DIRS_USED}/.compiler-fingerprint")
    endif ()
    if (_P101_FLAG_CACHE_INPUTS)
        set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
                ${_P101_FLAG_CACHE_INPUTS})
    endif ()
endif ()

# Helper to read flags into list
function(_p101_read_flags FILE OUTVAR)
    if (EXISTS "${FILE}")
        file(READ "${FILE}" _raw)
        string(REGEX REPLACE "[\r\n]+" " " _flat "${_raw}")
        separate_arguments(_tok NATIVE_COMMAND "${_flat}")
        set(${OUTVAR} "${_tok}" PARENT_SCOPE)
    else ()
        set(${OUTVAR} "" PARENT_SCOPE)
    endif ()
endfunction()

# Collect compile/link/analyzer flags
set(P101_EXTRA_CFLAGS "")
set(P101_EXTRA_LDFLAGS "")
set(P101_ANALYZER_ONLY_CFLAGS "")

if (_FLAG_DIRS_USED)
    # NOTE: coverage_flags.txt / profile_flags.txt are NOT read here — they are
    # instrumentation, applied ONLY when the user selects coverage/profiling
    # (see the opt-in block below). This loop is the always-on compiler set:
    # warnings, analysis, codegen/debug/opt, and hardening (compile + link).
    foreach (_ff IN ITEMS
            analyzer_flags.txt
            code_generation_flags.txt
            debug_flags.txt
            optimization_flags.txt
            warning_flags.txt
            hardening_link_flags.txt
            hardening_compiler_flags.txt
    )
        set(_p "${_FLAG_DIRS_USED}/${_ff}")
        _p101_read_flags("${_p}" _f)
        if (NOT _f)
            continue()
        endif ()

        if (_ff STREQUAL "analyzer_flags.txt")
            # These are GCC's -fanalyzer/-Wanalyzer-* flags (clang rejects
            # them at probe time, so its file is empty). They MUST also go
            # into the real compile flags — the analyzer runs during
            # compilation; keeping them "analyzer-only" meant GCC's static
            # analyzer never ran at all. Kept separately too for CSA.
            list(APPEND P101_ANALYZER_ONLY_CFLAGS ${_f})
            list(APPEND P101_EXTRA_CFLAGS ${_f})
        elseif (_ff MATCHES "hardening_compiler")
            list(APPEND P101_EXTRA_CFLAGS ${_f})
        elseif (_ff MATCHES "hardening_link")
            list(APPEND P101_EXTRA_LDFLAGS ${_f})
        elseif (_ff MATCHES "code_generation|debug|optimization|warning")
            list(APPEND P101_EXTRA_CFLAGS ${_f})
        endif ()
    endforeach ()
endif ()

message(STATUS "Extra CFLAGS: ${P101_EXTRA_CFLAGS}")
message(STATUS "Analyzer-only CFLAGS: ${P101_ANALYZER_ONLY_CFLAGS}")
message(STATUS "Extra LDFLAGS: ${P101_EXTRA_LDFLAGS}")

# =========================
# Per-file flag opt-outs
# =========================
# A flag can probe as supported on trivial code yet be *rejected* on a specific
# construct a particular file uses — e.g. -fharden-control-flow-redundancy hard-
# errors on any function that calls setjmp, which the setjmp wrappers do. Keep
# the flag ON for the whole project and disable it for ONLY the named files by
# appending its negation there; source COMPILE_OPTIONS are emitted after the
# target's, so the -fno-/-Wno- form wins (last-one-wins) for just that file.
#
# config.cmake format — one entry per file, whitespace-separated:
#     set(P101_FILE_FLAG_OPTOUTS "src/setjmp.c -fharden-control-flow-redundancy")
#
# The negation is added ONLY when the positive flag is actually in THIS
# compiler's applied set, so a compiler that never had the flag (clang, or a
# gcc too old for it) is never handed an unknown -fno-/-Wno- option.
if (DEFINED P101_FILE_FLAG_OPTOUTS AND P101_FILE_FLAG_OPTOUTS)
    foreach (_optentry IN LISTS P101_FILE_FLAG_OPTOUTS)
        string(STRIP "${_optentry}" _optentry)
        if (_optentry STREQUAL "")
            continue ()
        endif ()
        separate_arguments(_optparts NATIVE_COMMAND "${_optentry}")
        list(POP_FRONT _optparts _optfile)
        set(_neg_opts "")
        foreach (_flag IN LISTS _optparts)
            if (NOT "${_flag}" IN_LIST P101_EXTRA_CFLAGS)
                continue ()   # this compiler never got the flag; nothing to negate
            endif ()
            set(_neg "")
            if (_flag MATCHES "^-f" AND NOT _flag MATCHES "^-fno-")
                string(REGEX REPLACE "^-f" "-fno-" _neg "${_flag}")
            elseif (_flag MATCHES "^-W" AND NOT _flag MATCHES "^-Wno-")
                string(REGEX REPLACE "^-W" "-Wno-" _neg "${_flag}")
            endif ()
            string(REGEX REPLACE "=.*$" "" _neg "${_neg}")   # -fstrub=all -> -fno-strub
            if (_neg STREQUAL "")
                message(WARNING "P101_FILE_FLAG_OPTOUTS: no negation known for '${_flag}' "
                        "(file '${_optfile}'); leaving it applied.")
            else ()
                list(APPEND _neg_opts "${_neg}")
            endif ()
        endforeach ()
        if (_neg_opts)
            set_property(SOURCE "${_optfile}" APPEND PROPERTY COMPILE_OPTIONS ${_neg_opts})
            message(STATUS "Per-file opt-out: ${_optfile} -> ${_neg_opts}")
        endif ()
    endforeach ()
endif ()

# =========================
# Opt-in coverage / profiling (instrumentation)
# =========================
# OFF by default. Two independent, symmetric ways for the user to select it
# (either one turns it on), mirroring how sanitizers are chosen:
#   * the --coverage / --profile flags on setup / update / update-all /
#     change-compiler / build-all, which export P101_COVERAGE / P101_PROFILE; or
#   * a coverage.txt / profile.txt file in the project root holding a truthy
#     value (like sanitizers.txt).
# The flag SET is probed per compiler and lives in coverage_flags.txt /
# profile_flags.txt under .flags/<cc>/ — there is NO hardcoded --coverage/-pg
# here, so each compiler gets exactly the switches it accepts (e.g. gcc's
# -fprofile-abs-path is dropped on clang by the probe). Applied to every target
# (libraries and binaries) at compile AND link.

# TRUE when an env var holds a truthy value ("" / 0 / off / false / no => off)
macro(_p101_env_on _name _out)
    set(${_out} FALSE)
    if (DEFINED ENV{${_name}})
        set(_p101_val "$ENV{${_name}}")
        string(TOLOWER "${_p101_val}" _p101_val)
        if (NOT _p101_val STREQUAL "" AND NOT _p101_val STREQUAL "0"
                AND NOT _p101_val STREQUAL "off"
                AND NOT _p101_val STREQUAL "false"
                AND NOT _p101_val STREQUAL "no")
            set(${_out} TRUE)
        endif ()
    endif ()
endmacro()

# TRUE when a sentinel file exists and holds a truthy value (comments after #
# and all whitespace are stripped first)
macro(_p101_file_on _path _out)
    set(${_out} FALSE)
    if (EXISTS "${_path}")
        file(READ "${_path}" _p101_raw)
        string(REGEX REPLACE "#[^\n]*" "" _p101_raw "${_p101_raw}")
        string(REGEX REPLACE "[ \t\r\n]" "" _p101_raw "${_p101_raw}")
        string(TOLOWER "${_p101_raw}" _p101_raw)
        if (NOT _p101_raw STREQUAL "" AND NOT _p101_raw STREQUAL "0"
                AND NOT _p101_raw STREQUAL "off"
                AND NOT _p101_raw STREQUAL "false"
                AND NOT _p101_raw STREQUAL "no")
            set(${_out} TRUE)
        endif ()
    endif ()
endmacro()

_p101_env_on(P101_COVERAGE _p101_cov_env)
_p101_file_on("${CMAKE_CURRENT_SOURCE_DIR}/coverage.txt" _p101_cov_file)
if (_p101_cov_env OR _p101_cov_file)
    set(_p101_covflags "")
    if (_FLAG_DIRS_USED)
        _p101_read_flags("${_FLAG_DIRS_USED}/coverage_flags.txt" _p101_covflags)
    endif ()
    if (_p101_covflags)
        list(APPEND P101_EXTRA_CFLAGS  ${_p101_covflags})
        list(APPEND P101_EXTRA_LDFLAGS ${_p101_covflags})
        message(STATUS "Coverage selected (gcov): ${_p101_covflags}")
    else ()
        message(WARNING "Coverage selected, but no usable coverage flags for this "
                "compiler/target. Either .flags has not been (re)probed yet — run "
                "./update.sh or ./update-all.sh — or coverage is unsupported here. "
                "No coverage instrumentation applied.")
    endif ()
endif ()

_p101_env_on(P101_PROFILE _p101_prof_env)
_p101_file_on("${CMAKE_CURRENT_SOURCE_DIR}/profile.txt" _p101_prof_file)
if (_p101_prof_env OR _p101_prof_file)
    set(_p101_profflags "")
    if (_FLAG_DIRS_USED)
        _p101_read_flags("${_FLAG_DIRS_USED}/profile_flags.txt" _p101_profflags)
    endif ()
    if (_p101_profflags)
        list(APPEND P101_EXTRA_CFLAGS  ${_p101_profflags})
        list(APPEND P101_EXTRA_LDFLAGS ${_p101_profflags})
        message(STATUS "Profiling selected (gprof): ${_p101_profflags}")
    else ()
        message(WARNING "Profiling selected, but no usable profiling flags for this "
                "compiler/target. Either .flags has not been (re)probed yet — run "
                "./update.sh or ./update-all.sh — or profiling is unsupported here "
                "(e.g. gprof/-pg is unavailable on macOS; use a sampling profiler "
                "such as ./profile-report.sh). No profiling instrumentation applied.")
    endif ()
endif ()

# =========================
# Sanitizers from files
# =========================
string(REPLACE "," ";" _REQ_SAN_LIST "${SANITIZER_LIST}")
set(_P101_SANITIZER_COMPILE_OPTS "")
set(_P101_SANITIZER_LINK_OPTS "")

set(_SAN_OK TRUE)
if (APPLE AND _COMPILER_ID STREQUAL "GNU")
    set(_SAN_OK FALSE)
endif ()

if (_SAN_OK AND _FLAG_DIRS_USED)
    foreach (_s IN LISTS _REQ_SAN_LIST)
        string(STRIP "${_s}" _s)
        if (_s STREQUAL "")
            continue()
        endif ()
        string(TOLOWER "${_s}" _san)
        set(_file "${_FLAG_DIRS_USED}/${_san}_sanitizer_flags.txt")
        _p101_read_flags("${_file}" _sanflags)
        if (_sanflags)
            list(APPEND _P101_SANITIZER_COMPILE_OPTS ${_sanflags})
            list(APPEND _P101_SANITIZER_LINK_OPTS ${_sanflags})
        elseif (P101_ALLOW_MISSING_SANITIZERS)
            message(WARNING
                    "Requested sanitizer '${_san}' has no usable probed flags in "
                    "${_FLAG_DIRS_USED}; P101_ALLOW_MISSING_SANITIZERS is ON, "
                    "so this sanitizer will not be enabled.")
        else ()
            message(FATAL_ERROR
                    "Requested sanitizer '${_san}' has no usable probed flags in "
                    "${_FLAG_DIRS_USED}. Re-probe the flag cache with update.sh, "
                    "remove the unsupported sanitizer from SANITIZER_LIST, or set "
                    "P101_ALLOW_MISSING_SANITIZERS=ON only for an explicit "
                    "bring-up build.")
        endif ()
    endforeach ()
elseif (_REQ_SAN_LIST AND NOT _SAN_OK)
    message(STATUS
            "Requested sanitizers are disabled by the supported platform policy "
            "for ${_COMPILER_ID} on this host.")
endif ()

# The same -fsanitize= flag can legitimately appear in two sanitizer files
# (e.g. pointer-overflow is also part of undefined) — pass it once.
list(REMOVE_DUPLICATES _P101_SANITIZER_COMPILE_OPTS)
list(REMOVE_DUPLICATES _P101_SANITIZER_LINK_OPTS)

set(DETECTED_SANITIZERS "${_P101_SANITIZER_COMPILE_OPTS}" CACHE STRING "Effective sanitizer flags" FORCE)
message(STATUS "Detected sanitizer flags: ${DETECTED_SANITIZERS}")

# =========================
# Public include/link dirs
# =========================
set(PUBLIC_INC_DIR "${CMAKE_CURRENT_SOURCE_DIR}/include")
set(_P101_WORKSPACE_ROOT_CANDIDATES
        "${CMAKE_CURRENT_SOURCE_DIR}/../.."
        "${CMAKE_CURRENT_SOURCE_DIR}/.."
        "${CMAKE_CURRENT_SOURCE_DIR}/../../.."
)
set(_P101_LOCAL_LINK_DIRS "")
set(_P101_LOCAL_FALLBACK_LINK_DIRS "")
set(_P101_WORKSPACE_ROOT "")
get_filename_component(_P101_COMPILER_BASENAME "${CMAKE_C_COMPILER}" NAME)
foreach (_p101_root IN LISTS _P101_WORKSPACE_ROOT_CANDIDATES)
    get_filename_component(_p101_root_abs "${_p101_root}" ABSOLUTE)
    if (IS_DIRECTORY "${_p101_root_abs}/libraries")
        set(_P101_WORKSPACE_ROOT "${_p101_root_abs}")
        file(GLOB _p101_library_repos LIST_DIRECTORIES true "${_p101_root_abs}/libraries/lib_*")
        list(SORT _p101_library_repos)
        foreach (_p101_library_repo IN LISTS _p101_library_repos)
            get_filename_component(_p101_library_name "${_p101_library_repo}" NAME)
            if (_p101_library_name MATCHES "^lib_(posix|posix_optional|posix_xsi|unix)$")
                continue()
            endif ()
            if (IS_DIRECTORY "${_p101_library_repo}/build-${_P101_COMPILER_BASENAME}")
                list(APPEND _P101_LOCAL_LINK_DIRS "${_p101_library_repo}/build-${_P101_COMPILER_BASENAME}")
            endif ()
            if (EXISTS "${_p101_library_repo}/.last-build-dir")
                file(READ "${_p101_library_repo}/.last-build-dir" _p101_last_build_dir)
                string(STRIP "${_p101_last_build_dir}" _p101_last_build_dir)
                if (IS_DIRECTORY "${_p101_library_repo}/${_p101_last_build_dir}")
                    list(APPEND _P101_LOCAL_LINK_DIRS "${_p101_library_repo}/${_p101_last_build_dir}")
                endif ()
            endif ()
            file(GLOB _p101_library_build_dirs LIST_DIRECTORIES true "${_p101_library_repo}/build*")
            list(SORT _p101_library_build_dirs)
            foreach (_p101_library_build_dir IN LISTS _p101_library_build_dirs)
                if (IS_DIRECTORY "${_p101_library_build_dir}")
                    list(APPEND _P101_LOCAL_FALLBACK_LINK_DIRS "${_p101_library_build_dir}")
                endif ()
            endforeach ()
        endforeach ()
        break()
    endif ()
endforeach ()
list(REMOVE_DUPLICATES _P101_LOCAL_LINK_DIRS)
list(REMOVE_DUPLICATES _P101_LOCAL_FALLBACK_LINK_DIRS)
list(APPEND _P101_LOCAL_LINK_DIRS ${_P101_LOCAL_FALLBACK_LINK_DIRS})
list(REMOVE_DUPLICATES _P101_LOCAL_LINK_DIRS)

function(_p101_target_local_include_dirs OUT TARGET_NAME)
    set(_p101_target_dirs "")
    if (_P101_WORKSPACE_ROOT)
        foreach (_p101_link_token IN LISTS ${TARGET_NAME}_LINK_LIBRARIES)
            if (_p101_link_token MATCHES "^p101_([A-Za-z0-9_]+)$")
                set(_p101_dependency_repo "lib_${CMAKE_MATCH_1}")
                # p101_record is a second public library owned by
                # lib_tool_event rather than a standalone lib_record repo.
                if (_p101_link_token STREQUAL "p101_record")
                    set(_p101_dependency_repo "lib_tool_event")
                endif ()
                set(_p101_dependency_include
                        "${_P101_WORKSPACE_ROOT}/libraries/${_p101_dependency_repo}/include")
                if (IS_DIRECTORY "${_p101_dependency_include}")
                    list(APPEND _p101_target_dirs "${_p101_dependency_include}")
                endif ()
            endif ()
        endforeach ()
    endif ()
    list(REMOVE_DUPLICATES _p101_target_dirs)
    set(${OUT} "${_p101_target_dirs}" PARENT_SCOPE)
endfunction()

set(_P101_REQUIRED_LOCAL_INCLUDE_DIRS "")
foreach (_p101_target IN LISTS LIBRARY_TARGETS EXECUTABLE_TARGETS)
    _p101_target_local_include_dirs(_p101_target_dirs "${_p101_target}")
    list(APPEND _P101_REQUIRED_LOCAL_INCLUDE_DIRS ${_p101_target_dirs})
endforeach ()
list(REMOVE_DUPLICATES _P101_REQUIRED_LOCAL_INCLUDE_DIRS)

set(_INC_CANDIDATES
        "${PUBLIC_INC_DIR}"
        ${_P101_REQUIRED_LOCAL_INCLUDE_DIRS}
        ${P101_PUBLIC_INCLUDE_DIRS_LIST}
        "/usr/local/include"
)
set(_LIB_CANDIDATES
        ${_P101_LOCAL_LINK_DIRS}
        ${P101_PUBLIC_LINK_DIRS_LIST}
        "/usr/local/lib" "/usr/local/lib64"
)
set(_INSTALL_LIB_CANDIDATES
        ${P101_PUBLIC_LINK_DIRS_LIST}
        "/usr/local/lib" "/usr/local/lib64"
)
if (APPLE)
    list(APPEND _INC_CANDIDATES /opt/homebrew/include /opt/local/include)
    list(APPEND _LIB_CANDIDATES /opt/homebrew/lib /opt/local/lib)
    list(APPEND _INSTALL_LIB_CANDIDATES /opt/homebrew/lib /opt/local/lib)
endif ()
list(REMOVE_DUPLICATES _INC_CANDIDATES)
list(REMOVE_DUPLICATES _LIB_CANDIDATES)
list(REMOVE_DUPLICATES _INSTALL_LIB_CANDIDATES)

_p101_filter_existing_dirs(P101_PUBLIC_INCLUDE_DIRS_EXISTING ${_INC_CANDIDATES})
_p101_filter_existing_dirs(P101_PUBLIC_LINK_DIRS_EXISTING ${_LIB_CANDIDATES})
_p101_filter_existing_dirs(P101_INSTALL_LINK_DIRS_EXISTING ${_INSTALL_LIB_CANDIDATES})

if (P101_PUBLIC_LINK_DIRS_EXISTING)
    set(CMAKE_BUILD_RPATH "${P101_PUBLIC_LINK_DIRS_EXISTING}")
endif ()
if (P101_INSTALL_LINK_DIRS_EXISTING)
    set(CMAKE_INSTALL_RPATH "${P101_INSTALL_LINK_DIRS_EXISTING}")
endif ()
set(CMAKE_INSTALL_RPATH_USE_LINK_PATH OFF)

message(STATUS "Public include dirs used: ${P101_PUBLIC_INCLUDE_DIRS_EXISTING}")
message(STATUS "Public link dirs used:    ${P101_PUBLIC_LINK_DIRS_EXISTING}")
message(STATUS "Install rpath dirs used:  ${P101_INSTALL_LINK_DIRS_EXISTING}")

# --- Classify include dirs into project vs external (system)
set(P101_PROJECT_INC_DIR "")
if (IS_DIRECTORY "${PUBLIC_INC_DIR}")
    set(P101_PROJECT_INC_DIR "${PUBLIC_INC_DIR}")
endif ()

set(_LOCAL_INC_DIRS ${_P101_REQUIRED_LOCAL_INCLUDE_DIRS})
if (P101_PROJECT_INC_DIR)
    list(REMOVE_ITEM _LOCAL_INC_DIRS "${P101_PROJECT_INC_DIR}")
endif ()
list(REMOVE_DUPLICATES _LOCAL_INC_DIRS)

set(_EXT_INC_DIRS "${P101_PUBLIC_INCLUDE_DIRS_EXISTING}")
if (P101_PROJECT_INC_DIR)
    list(REMOVE_ITEM _EXT_INC_DIRS "${P101_PROJECT_INC_DIR}")
endif ()
foreach (_d IN LISTS _LOCAL_INC_DIRS)
    list(REMOVE_ITEM _EXT_INC_DIRS "${_d}")
endforeach ()
list(REMOVE_DUPLICATES _EXT_INC_DIRS)

# CMake's IMPLICIT_DEPENDS scanner used by the per-TU static-analyzer custom
# commands only sees directory-scoped include paths. Keep the target-scoped
# include_directories below for correct public/private usage, but mirror the
# same search paths at directory scope so editing a project header causes the
# static analyzer stamp to rebuild.
if (P101_PROJECT_INC_DIR)
    include_directories("${P101_PROJECT_INC_DIR}")
endif ()
if (_LOCAL_INC_DIRS)
    include_directories(${_LOCAL_INC_DIRS})
endif ()
if (_EXT_INC_DIRS)
    include_directories(SYSTEM ${_EXT_INC_DIRS})
endif ()

# Prebuild convenience strings for custom commands (best-effort; used only for echo lines)
set(_MAC_SYSROOT_CMD "")
if (APPLE AND MAC_SYSROOT)
    set(_MAC_SYSROOT_CMD "-isysroot \"${MAC_SYSROOT}\"")
endif ()
set(_EXT_ISYSTEM_LIST)
foreach (_d IN LISTS _EXT_INC_DIRS)
    list(APPEND _EXT_ISYSTEM_LIST -isystem "\"${_d}\"")
endforeach ()
string(JOIN " " _EXT_ISYSTEM_CMD ${_EXT_ISYSTEM_LIST})

set(_PROJ_I_CMD "")
if (P101_PROJECT_INC_DIR)
    set(_PROJ_I_CMD "-I\"${P101_PROJECT_INC_DIR}\"")
endif ()
set(_LOCAL_I_LIST)
foreach (_d IN LISTS _LOCAL_INC_DIRS)
    list(APPEND _LOCAL_I_LIST -I "\"${_d}\"")
endforeach ()
string(JOIN " " _LOCAL_I_CMD ${_LOCAL_I_LIST})

# External library policy and logical-token resolution are owned separately.
include("${P101_CMAKE_DIR}/P101Linking.cmake")

# =========================
# Targets from config.cmake
# =========================
set(LIBRARY_TARGETS "${LIBRARY_TARGETS}")
set(EXECUTABLE_TARGETS "${EXECUTABLE_TARGETS}")
# Drop blank / whitespace-only target entries. CMake's regex engine has no
# \t or \s atom (inside a class, "\t" is the literal chars '\' and 't'), so a
# "^[ \t]*$" filter would silently miss tab-only entries. string(STRIP)
# removes surrounding whitespace INCLUDING tabs, so stripping each entry and
# dropping the empties is both correct and trims stray whitespace off names.
foreach (_lst LIBRARY_TARGETS EXECUTABLE_TARGETS)
    set(_kept "")
    foreach (_e IN LISTS ${_lst})
        string(STRIP "${_e}" _e)
        if (NOT _e STREQUAL "")
            list(APPEND _kept "${_e}")
        endif ()
    endforeach ()
    set(${_lst} "${_kept}")
endforeach ()

set(_ALL_SOURCES_FOR_FORMAT)
set(_ALL_HEADERS_FOR_INSTALL)
set(P101_PRIMARY_TARGETS)

function(_p101_abs_list OUT)
    set(_o "")
    foreach (_s IN LISTS ARGN)
        if (IS_ABSOLUTE "${_s}")
            list(APPEND _o "${_s}")
        else ()
            list(APPEND _o "${CMAKE_CURRENT_SOURCE_DIR}/${_s}")
        endif ()
    endforeach ()
    set(${OUT} "${_o}" PARENT_SCOPE)
endfunction()

# =========================
# Sanitizer runtime defaults (only when a sanitizer is active AND an executable
# is built). Bake sane defaults into the program via the weak
# __<san>_default_options() hooks so a student just runs the instrumented binary
# and gets a symbolized stack trace that STOPS at the first error — no
# UBSAN_OPTIONS/TSAN_OPTIONS env setup. Environment variables still override.
#
# Compiled to a STANDALONE object and linked in (not a normal target source), so
# it never enters compile_commands.json and is invisible to cppcheck / the
# analyze stage / clang-tidy / clang-format. Added to executables only, so it
# cannot clash with a copy in a shared library.
# =========================
set(_P101_SAN_OBJ "")
if (_P101_SANITIZER_COMPILE_OPTS AND EXECUTABLE_TARGETS)
    if (PROJECT_LANGUAGE STREQUAL "CXX")
        set(_P101_SAN_SRC "${CMAKE_BINARY_DIR}/p101_sanitizer_defaults.cpp")
        set(_P101_SAN_CC  "${CMAKE_CXX_COMPILER}")
        set(_P101_SAN_EXTERN "extern \"C\" ")
    else ()
        set(_P101_SAN_SRC "${CMAKE_BINARY_DIR}/p101_sanitizer_defaults.c")
        set(_P101_SAN_CC  "${CMAKE_C_COMPILER}")
        set(_P101_SAN_EXTERN "")
    endif ()
    file(WRITE "${_P101_SAN_SRC}"
"/* Generated by P101 CMake - default sanitizer runtime options. Compiled
   standalone and linked in, so it is NOT part of the analyzed sources.
   Environment variables (UBSAN_OPTIONS, TSAN_OPTIONS, ...) override these. */
${_P101_SAN_EXTERN}const char *__ubsan_default_options(void) { return \"print_stacktrace=1:halt_on_error=1\"; }
${_P101_SAN_EXTERN}const char *__tsan_default_options(void)  { return \"halt_on_error=1\"; }
")
    set(_P101_SAN_OBJ "${CMAKE_BINARY_DIR}/p101_sanitizer_defaults.o")
    add_custom_command(
            OUTPUT "${_P101_SAN_OBJ}"
            COMMAND "${_P101_SAN_CC}" -c -fPIC -w "${_P101_SAN_SRC}" -o "${_P101_SAN_OBJ}"
            DEPENDS "${_P101_SAN_SRC}"
            COMMENT "Building sanitizer runtime defaults (UBSan/TSan)"
            VERBATIM)
    set_source_files_properties("${_P101_SAN_OBJ}" PROPERTIES EXTERNAL_OBJECT TRUE GENERATED TRUE)
    message(STATUS "Sanitizer runtime defaults will be linked into executables.")
endif ()

# On macOS, Clang's driver may record its dynamic ASan runtime after user
# shared libraries in the Mach-O load-command order. ASan then aborts before
# main because its interceptors were initialized too late. Resolve the runtime
# belonging to this exact compiler and link it explicitly before application
# libraries; the driver's later implicit copy is coalesced by the linker.
set(_ASAN_RPATH "")
set(_P101_ASAN_RUNTIME "")
if (APPLE AND _COMPILER_ID MATCHES "Clang")
    execute_process(
            COMMAND ${_COMPILER} --print-resource-dir
            OUTPUT_VARIABLE _CLANG_RESOURCE_DIR
            OUTPUT_STRIP_TRAILING_WHITESPACE
            ERROR_QUIET)
    set(_ASAN_RPATH "${_CLANG_RESOURCE_DIR}/lib/darwin")
    list(FIND _P101_SANITIZER_COMPILE_OPTS "-fsanitize=address"
            _P101_ASAN_FLAG_INDEX)
    if (NOT _P101_ASAN_FLAG_INDEX EQUAL -1)
        set(_P101_ASAN_RUNTIME
                "${_ASAN_RPATH}/libclang_rt.asan_osx_dynamic.dylib")
        if (NOT EXISTS "${_P101_ASAN_RUNTIME}")
            message(FATAL_ERROR
                    "AddressSanitizer is active, but its compiler-matched runtime "
                    "does not exist: ${_P101_ASAN_RUNTIME}")
        endif ()
    endif ()
endif ()

# Libraries
foreach (_lib IN LISTS LIBRARY_TARGETS)
    if (NOT ${_lib}_SOURCES)
        message(FATAL_ERROR "Target ${_lib} has no <name>_SOURCES in config.cmake")
    endif ()
    _p101_abs_list(_srcs_abs ${${_lib}_SOURCES})
    list(APPEND _ALL_SOURCES_FOR_FORMAT ${_srcs_abs})

    add_library(${_lib} SHARED ${_srcs_abs} ${${_lib}_HEADERS})

    # Project includes as normal (-I)
    if (P101_PROJECT_INC_DIR)
        target_include_directories(${_lib} PUBLIC "${P101_PROJECT_INC_DIR}")
    endif ()
    # Local p101 workspace libraries as normal (-I), before installed prefixes.
    _p101_target_local_include_dirs(_p101_target_include_dirs "${_lib}")
    if (_p101_target_include_dirs)
        target_include_directories(${_lib} PUBLIC ${_p101_target_include_dirs})
    endif ()
    # External as SYSTEM (-isystem)
    if (_EXT_INC_DIRS)
        target_include_directories(${_lib} SYSTEM PUBLIC ${_EXT_INC_DIRS})
    endif ()

    target_compile_options(${_lib} PRIVATE ${P101_EFFECTIVE_STANDARD_FLAGS} ${P101_EXTRA_CFLAGS} ${_P101_SANITIZER_COMPILE_OPTS})
    target_link_options(${_lib} PRIVATE ${P101_EXTRA_LDFLAGS} ${_P101_SANITIZER_LINK_OPTS})

    if (${_lib}_LINK_LIBRARIES)
        message(STATUS "[link] ${_lib} tokens from config.cmake: ${${_lib}_LINK_LIBRARIES}")
        _p101_resolve_link_items(_RESOLVED_LIBS ${${_lib}_LINK_LIBRARIES})
        if (_RESOLVED_LIBS)
            message(STATUS "[link] ${_lib} resolved libs: ${_RESOLVED_LIBS}")
            target_link_libraries(${_lib} PRIVATE ${_RESOLVED_LIBS})
        endif ()
    endif ()

    # Absolutize headers so the clang-format loop's source-dir prefix check
    # doesn't silently drop headers listed with relative paths.
    _p101_abs_list(_hdrs_abs ${${_lib}_HEADERS})
    list(APPEND _ALL_HEADERS_FOR_INSTALL ${_hdrs_abs})
    list(APPEND P101_PRIMARY_TARGETS ${_lib})
endforeach ()

# Executables
foreach (_exe IN LISTS EXECUTABLE_TARGETS)
    if (NOT ${_exe}_SOURCES)
        message(FATAL_ERROR "Target ${_exe} has no <name>_SOURCES in config.cmake")
    endif ()
    _p101_abs_list(_srcs_abs ${${_exe}_SOURCES})
    list(APPEND _ALL_SOURCES_FOR_FORMAT ${_srcs_abs})

    add_executable(${_exe} ${_srcs_abs})
    if (DEFINED ${_exe}_OUTPUT_NAME AND NOT "${${_exe}_OUTPUT_NAME}" STREQUAL "")
        set_target_properties(${_exe} PROPERTIES OUTPUT_NAME "${${_exe}_OUTPUT_NAME}")
    endif ()

    # Project includes as normal (-I)
    if (P101_PROJECT_INC_DIR)
        target_include_directories(${_exe} PRIVATE "${P101_PROJECT_INC_DIR}")
    endif ()
    # Local p101 workspace libraries as normal (-I), before installed prefixes.
    _p101_target_local_include_dirs(_p101_target_include_dirs "${_exe}")
    if (_p101_target_include_dirs)
        target_include_directories(${_exe} PRIVATE ${_p101_target_include_dirs})
    endif ()
    # External as SYSTEM (-isystem)
    if (_EXT_INC_DIRS)
        target_include_directories(${_exe} SYSTEM PRIVATE ${_EXT_INC_DIRS})
    endif ()

    target_compile_options(${_exe} PRIVATE ${P101_EFFECTIVE_STANDARD_FLAGS} ${P101_EXTRA_CFLAGS} ${_P101_SANITIZER_COMPILE_OPTS})
    target_link_options(${_exe} PRIVATE ${P101_EXTRA_LDFLAGS} ${_P101_SANITIZER_LINK_OPTS})

    # Link the compiled-in sanitizer default-options object (executables only).
    if (_P101_SAN_OBJ)
        target_sources(${_exe} PRIVATE ${_P101_SAN_OBJ})
    endif ()

    if (_P101_ASAN_RUNTIME)
        target_link_libraries(${_exe} PRIVATE "${_P101_ASAN_RUNTIME}")
    endif ()

    if (${_exe}_LINK_LIBRARIES)
        message(STATUS "[link] ${_exe} tokens from config.cmake: ${${_exe}_LINK_LIBRARIES}")
        _p101_resolve_link_items(_RESOLVED_LIBS ${${_exe}_LINK_LIBRARIES})
        if (_RESOLVED_LIBS)
            message(STATUS "[link] ${_exe} resolved libs: ${_RESOLVED_LIBS}")
            target_link_libraries(${_exe} PRIVATE ${_RESOLVED_LIBS})
        endif ()
    endif ()

    list(APPEND P101_PRIMARY_TARGETS ${_exe})
endforeach ()

# A source (or header) listed by more than one target must not create two
# custom commands with the same stamp OUTPUT — that is a fatal configure
# error in the clang-format and CSA loops below.
list(REMOVE_DUPLICATES _ALL_SOURCES_FOR_FORMAT)
list(REMOVE_DUPLICATES _ALL_HEADERS_FOR_INSTALL)

if(APPLE AND _COMPILER_ID MATCHES "Clang")
    if(_P101_ASAN_RUNTIME AND IS_DIRECTORY "${_ASAN_RPATH}")
        foreach(_tgt IN LISTS LIBRARY_TARGETS EXECUTABLE_TARGETS)
            # APPEND: the targets already inherited /usr/local/lib etc. from
            # CMAKE_BUILD_RPATH/CMAKE_INSTALL_RPATH — replacing those would
            # break @rpath resolution of the installed p101 dylibs on macOS.
            set_property(TARGET ${_tgt} APPEND PROPERTY BUILD_RPATH "${_ASAN_RPATH}")
            set_property(TARGET ${_tgt} APPEND PROPERTY INSTALL_RPATH "${_ASAN_RPATH}")
        endforeach()
        message(STATUS "ASan rpath added: ${_ASAN_RPATH}")
    elseif(_P101_ASAN_RUNTIME)
        message(STATUS "ASan rpath dir not found: ${_ASAN_RPATH}")
    endif()
endif()

# Runtime-only configurations intentionally stop after defining the primary
# targets and their installation/receipt rules.  This keeps installation safe
# without paying for the strict analyzer pipeline twice.
if (P101_RUNTIME_ONLY)
    if (_P101_SANITIZER_COMPILE_OPTS OR _P101_SANITIZER_LINK_OPTS)
        message(FATAL_ERROR
                "P101_RUNTIME_ONLY requires a sanitizer-free configuration.")
    endif ()
    message(STATUS
            "P101_RUNTIME_ONLY: building installable targets without quality-analysis targets.")
    include("${P101_CMAKE_DIR}/P101Install.cmake")
    include("${P101_CMAKE_DIR}/P101Summary.cmake")
    return()
endif ()

# =========================
# clang-format (check-before-compile; mutation is explicit)
# =========================
if (CLANG_FORMAT_NAME AND _ALL_SOURCES_FOR_FORMAT)
    set(_FORMAT_CHECK_STAMPS)
    set(_P101_FORMAT_FILES)
    foreach (_abs IN LISTS _ALL_SOURCES_FOR_FORMAT _ALL_HEADERS_FOR_INSTALL)
        if (NOT _abs)
            continue()
        endif ()
        # string(FIND), not MATCHES: the source dir path is not a regex, and
        # metacharacters in it (e.g. a parent folder named "C++") must not
        # change the result.
        string(FIND "${_abs}" "${CMAKE_CURRENT_SOURCE_DIR}/" _pfx)
        if (NOT _pfx EQUAL 0)
            message(FATAL_ERROR "Source '${_abs}' is outside the project directory "
                    "(${CMAKE_CURRENT_SOURCE_DIR}). All sources must be in-tree; an "
                    "out-of-tree path almost always indicates a config.cmake error.")
        endif ()
        file(RELATIVE_PATH _rel "${CMAKE_CURRENT_SOURCE_DIR}" "${_abs}")
        get_filename_component(_rel_dir "${_rel}" DIRECTORY)
        set(_stamp "${CMAKE_BINARY_DIR}/.format-check/${_rel}.stamp")
        add_custom_command(
                OUTPUT "${_stamp}"
                COMMAND ${CMAKE_COMMAND} -E make_directory "${CMAKE_BINARY_DIR}/.format-check/${_rel_dir}"
                COMMAND ${CMAKE_COMMAND} -E echo "${ICON} checking: ${CLANG_FORMAT_NAME} --dry-run --Werror -style=file ${_abs}"
                COMMAND "${CLANG_FORMAT_NAME}" --dry-run --Werror -style=file "${_abs}"
                COMMAND ${CMAKE_COMMAND} -E touch "${_stamp}"
                DEPENDS "${_abs}"
                VERBATIM
        )
        list(APPEND _FORMAT_CHECK_STAMPS "${_stamp}")
        list(APPEND _P101_FORMAT_FILES "${_abs}")
    endforeach ()
    if (_FORMAT_CHECK_STAMPS)
        add_custom_target(${PROJECT_NAME}_format_check_all
                DEPENDS ${_FORMAT_CHECK_STAMPS})
        add_custom_target(${PROJECT_NAME}_format_all
                COMMAND ${CMAKE_COMMAND} -E echo
                "${ICON} formatting tracked project sources with ${CLANG_FORMAT_NAME}"
                COMMAND "${CLANG_FORMAT_NAME}" -i -style=file ${_P101_FORMAT_FILES}
                VERBATIM)
        foreach (_tgt IN LISTS LIBRARY_TARGETS EXECUTABLE_TARGETS)
            add_dependencies(${_tgt} ${PROJECT_NAME}_format_check_all)
        endforeach ()
    endif ()
endif ()

# =========================
# clang-tidy DB sanitizer (JSON-safe, via Python)
# =========================
set(P101_TIDY_DB_DIR "${CMAKE_BINARY_DIR}/tidy_cc")
set(P101_TIDY_DB "${P101_TIDY_DB_DIR}/compile_commands.json")

set(SAN_PY "${P101_CMAKE_DIR}/SanitizeCompileCommands.py")
if (UNIX)
    execute_process(COMMAND chmod +x "${SAN_PY}" ERROR_QUIET)
endif ()

add_custom_command(
        OUTPUT "${P101_TIDY_DB}"
        COMMAND ${CMAKE_COMMAND} -E make_directory "${P101_TIDY_DB_DIR}"
        COMMAND ${CMAKE_COMMAND} -E echo "${ICON} running: ${PYTHON3_EXECUTABLE} ${SAN_PY} ${CMAKE_BINARY_DIR}/compile_commands.json ${P101_TIDY_DB}"
        COMMAND "${PYTHON3_EXECUTABLE}" "${SAN_PY}" "${CMAKE_BINARY_DIR}/compile_commands.json" "${P101_TIDY_DB}"
        DEPENDS "${CMAKE_BINARY_DIR}/compile_commands.json" "${SAN_PY}"
        VERBATIM
        COMMENT "Sanitizing compile_commands.json for clang-tidy (JSON-safe)"
)
add_custom_target(${PROJECT_NAME}_tidy_db DEPENDS "${P101_TIDY_DB}")

# Ensure all primary targets generate tidy DB first (sanitized)
foreach (_tgt IN LISTS LIBRARY_TARGETS EXECUTABLE_TARGETS)
    add_dependencies(${_tgt} ${PROJECT_NAME}_tidy_db)
endforeach ()

# =========================
# clang-tidy (AFTER analyze)
# =========================
if (P101_TIDY_CHECKS STREQUAL "")
    set(P101_TIDY_CHECKS_LIST ${P101_TIDY_DEFAULT_CHECKS})
    if (P101_TIDY_EXTRA_CHECKS_LIST)
        list(APPEND P101_TIDY_CHECKS_LIST ${P101_TIDY_EXTRA_CHECKS_LIST})
    endif ()
    string(JOIN "," P101_TIDY_CHECKS ${P101_TIDY_CHECKS_LIST})
endif ()

# Default tidy header filter: only our project (src/ and include/) unless user overrides.
# -header-filter is a regex, and CMAKE_CURRENT_SOURCE_DIR / PUBLIC_INC_DIR are
# filesystem paths that can contain regex metacharacters (a folder named "C++",
# a version like "lib.1", a path with "(x86)"). Interpolating them raw makes '.'
# a wildcard and '+', '(' change the match. Escape ERE metacharacters so the
# paths are matched literally. The class lists ] first (literal in a class);
# \\0 back-references the whole match, prefixing each metachar with a backslash.
if (P101_HEADER_FILTER STREQUAL "" OR P101_HEADER_FILTER STREQUAL ".*")
    set(_p101_ere_meta "[][.^$|()\\\\{}*+?]")
    string(REGEX REPLACE "${_p101_ere_meta}" "\\\\\\0" _p101_src_re "${CMAKE_CURRENT_SOURCE_DIR}")
    string(REGEX REPLACE "${_p101_ere_meta}" "\\\\\\0" _p101_pub_re "${PUBLIC_INC_DIR}")
    set(P101_HEADER_FILTER "^(${_p101_src_re}/src|${_p101_pub_re})/.*")
endif ()

# Build shared args for clang-tidy
set(_P101_TIDY_ARGS
        "-header-filter=${P101_HEADER_FILTER}"
        "-checks=${P101_TIDY_CHECKS}"
        "-quiet"
        "--format-style=file"
)
if (NOT P101_TIDY_WARNINGS_AS_ERRORS STREQUAL "")
    list(APPEND _P101_TIDY_ARGS "-warnings-as-errors=${P101_TIDY_WARNINGS_AS_ERRORS}")
endif ()

foreach (_feature_define IN LISTS P101_FEATURE_TEST_FLAGS)
    list(APPEND _P101_TIDY_ARGS "--extra-arg-before=${_feature_define}")
endforeach ()

# Our includes
if (P101_PROJECT_INC_DIR)
    list(APPEND _P101_TIDY_ARGS "--extra-arg-before=-I${P101_PROJECT_INC_DIR}")
endif ()
foreach (_d IN LISTS _EXT_INC_DIRS)
    list(APPEND _P101_TIDY_ARGS "--extra-arg-before=-isystem" "--extra-arg-before=${_d}")
endforeach ()
if (APPLE AND MAC_SYSROOT)
    list(APPEND _P101_TIDY_ARGS "--extra-arg-before=-isysroot" "--extra-arg-before=${MAC_SYSROOT}")
endif ()
if (P101_TIDY_EXTRA_ARGS_LIST)
    list(APPEND _P101_TIDY_ARGS ${P101_TIDY_EXTRA_ARGS_LIST})
endif ()

# --- Robust arg/file passing for clang-tidy (via .cmake manifests)
set(_TIDY_ARGS_CMAKE "${CMAKE_BINARY_DIR}/tidy_args.cmake")
set(_TIDY_FILES_CMAKE "${CMAKE_BINARY_DIR}/tidy_files.cmake")

file(WRITE "${_TIDY_ARGS_CMAKE}" "set(P101_TIDY_ARGS_LIST\n")
foreach (_a IN LISTS _P101_TIDY_ARGS)
    string(REPLACE "\\" "\\\\" _a_esc "${_a}")
    string(REPLACE "\"" "\\\"" _a_esc "${_a_esc}")
    file(APPEND "${_TIDY_ARGS_CMAKE}" "  \"${_a_esc}\"\n")
endforeach ()
file(APPEND "${_TIDY_ARGS_CMAKE}" ")\n")

set(_TIDY_FILES)
foreach (_abs IN LISTS _ALL_SOURCES_FOR_FORMAT)
    string(FIND "${_abs}" "${CMAKE_CURRENT_SOURCE_DIR}/" _pfx)
    if (_pfx EQUAL 0)
        list(APPEND _TIDY_FILES "${_abs}")
    else ()
        message(FATAL_ERROR "Source '${_abs}' is outside the project directory "
                "(${CMAKE_CURRENT_SOURCE_DIR}). All sources must be in-tree; an "
                "out-of-tree path almost always indicates a config.cmake error.")
    endif ()
endforeach ()
file(WRITE "${_TIDY_FILES_CMAKE}" "set(P101_TIDY_FILES_LIST\n")
foreach (_f IN LISTS _TIDY_FILES)
    string(REPLACE "\\" "\\\\" _f_esc "${_f}")
    string(REPLACE "\"" "\\\"" _f_esc "${_f_esc}")
    file(APPEND "${_TIDY_FILES_CMAKE}" "  \"${_f_esc}\"\n")
endforeach ()
file(APPEND "${_TIDY_FILES_CMAKE}" ")\n")

set(RUN_TIDY_SCRIPT "${P101_CMAKE_DIR}/RunClangTidyOverList.cmake")

add_custom_target(${PROJECT_NAME}_tidy_all
        ALL
        COMMAND ${CMAKE_COMMAND}
        -DCLANG_TIDY_EXEC=${CLANG_TIDY_NAME}
        -DDB=${P101_TIDY_DB_DIR}
        -DARGS_CMAKE=${_TIDY_ARGS_CMAKE}
        -DFILES_CMAKE=${_TIDY_FILES_CMAKE}
        -P "${RUN_TIDY_SCRIPT}"
        DEPENDS ${PROJECT_NAME}_tidy_db
        WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
        VERBATIM
)

# =========================
# Generic "ANALYZE" stage (syntax-only, using compile_commands.json)
# =========================
set(P101_ANALYZE_DIR "${CMAKE_BINARY_DIR}/analyze")
file(MAKE_DIRECTORY "${P101_ANALYZE_DIR}")

set(P101_ANALYZE_DB_SCRIPT "${P101_CMAKE_DIR}/RunAnalyzeFromCompileDB.py")
if (UNIX)
    execute_process(COMMAND chmod +x "${P101_ANALYZE_DB_SCRIPT}" ERROR_QUIET)
endif()

add_custom_target(${PROJECT_NAME}_analyze_all ALL
        COMMAND ${CMAKE_COMMAND} -E echo "${ICON} running analyze stage from compile_commands.json"
        COMMAND "${PYTHON3_EXECUTABLE}" "${P101_ANALYZE_DB_SCRIPT}"
        "${CMAKE_BINARY_DIR}/compile_commands.json"
        "${P101_ANALYZE_DIR}"
        $<IF:$<BOOL:${P101_ANALYZE_FAIL_ON_DIAGNOSTICS}>,1,0>
        DEPENDS "${CMAKE_BINARY_DIR}/compile_commands.json" "${P101_ANALYZE_DB_SCRIPT}"
        WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
        USES_TERMINAL
        COMMENT "Per-TU analyze stage (syntax-only) using flags from compile DB"
)

# ===== ENFORCE ORDER: compile -> analyze -> tidy =====
# (guarded: add_dependencies() with no dependency arguments is a hard
# configure error when a config.cmake defines no targets)
if (P101_PRIMARY_TARGETS)
    add_dependencies(${PROJECT_NAME}_analyze_all ${LIBRARY_TARGETS} ${EXECUTABLE_TARGETS})
endif ()
add_dependencies(${PROJECT_NAME}_tidy_all ${PROJECT_NAME}_analyze_all)

# =========================
# On-demand code fix ("format" target): clang-tidy --fix (FULL check set),
# then clang-format -i.  NOT part of the build — a normal compile keeps tidy as
# a CHECK that fails on diagnostics.  Fixes are applied ONLY when explicitly
# requested:   cmake --build <dir> --target format   (./build.sh --format)
# =========================
# 1) clang-tidy --fix over the SAME sanitized DB / args / file list as the check.
add_custom_target(${PROJECT_NAME}_tidy_fix
        COMMAND ${CMAKE_COMMAND}
        -DCLANG_TIDY_EXEC=${CLANG_TIDY_NAME}
        -DDB=${P101_TIDY_DB_DIR}
        -DARGS_CMAKE=${_TIDY_ARGS_CMAKE}
        -DFILES_CMAKE=${_TIDY_FILES_CMAKE}
        -DFIX=ON
        -P "${RUN_TIDY_SCRIPT}"
        DEPENDS ${PROJECT_NAME}_tidy_db
        WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
        VERBATIM
        COMMENT "clang-tidy --fix (full check set) over the project")

# 2) clang-format pass, ordered AFTER tidy --fix (fixes can disturb layout).
#    Clean the file list (drop empty entries) so a single invocation is safe.
set(_P101_FMT_FILES)
foreach (_f IN LISTS _ALL_SOURCES_FOR_FORMAT _ALL_HEADERS_FOR_INSTALL)
    if (_f)
        list(APPEND _P101_FMT_FILES "${_f}")
    endif ()
endforeach ()

if (CLANG_FORMAT_NAME AND _P101_FMT_FILES)
    # DEPENDS runs tidy_fix first; this target's own COMMANDs run after -> format last.
    add_custom_target(format
            COMMAND ${CMAKE_COMMAND} -E echo "${ICON} clang-format -i (after clang-tidy --fix)"
            COMMAND "${CLANG_FORMAT_NAME}" -i -style=file ${_P101_FMT_FILES}
            DEPENDS ${PROJECT_NAME}_tidy_fix
            WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
            VERBATIM
            COMMENT "Apply clang-tidy fixes, then clang-format")
else ()
    add_custom_target(format
            COMMAND ${CMAKE_COMMAND} -E echo "clang-format not found — applied clang-tidy --fix only"
            DEPENDS ${PROJECT_NAME}_tidy_fix
            VERBATIM
            COMMENT "Apply clang-tidy fixes (clang-format unavailable)")
endif ()


# =========================
# cppcheck — ALWAYS runs (REQUIRED at configure)
# =========================
function(_p101_cppcheck_std OUT_STD OUT_LANG)
    # Prefer the std from STANDARD_FLAGS (what the compiler actually uses);
    # normalize gnu dialects to the base standard for cppcheck.
    if (NOT P101_STD_FLAG STREQUAL "")
        string(REPLACE "-std=" "" _cfg_std "${P101_STD_FLAG}")
        string(REPLACE "gnu++" "c++" _cfg_std "${_cfg_std}")
        string(REGEX REPLACE "^gnu" "c" _cfg_std "${_cfg_std}")
        if (_cfg_std MATCHES "^(c[0-9]+|c\\+\\+[0-9]+)$")
            if (PROJECT_LANGUAGE STREQUAL "CXX")
                set(${OUT_LANG} "c++" PARENT_SCOPE)
            else ()
                set(${OUT_LANG} "c" PARENT_SCOPE)
            endif ()
            set(${OUT_STD} "${_cfg_std}" PARENT_SCOPE)
            return()
        endif ()
    endif ()
    if (PROJECT_LANGUAGE STREQUAL "CXX")
        if (DEFINED CMAKE_CXX_STANDARD AND NOT "${CMAKE_CXX_STANDARD}" STREQUAL "")
            set(_std "c++${CMAKE_CXX_STANDARD}")
        else ()
            set(_std "c++17")
        endif ()
        set(_lang "c++")
    else ()
        if (DEFINED CMAKE_C_STANDARD AND NOT "${CMAKE_C_STANDARD}" STREQUAL "")
            set(_cstd "${CMAKE_C_STANDARD}")
        else ()
            set(_cstd 17)
        endif ()
        if (_cstd GREATER_EQUAL 23)
            set(_std "c23")
        elseif (_cstd GREATER_EQUAL 17)
            set(_std "c17")
        elseif (_cstd GREATER_EQUAL 11)
            set(_std "c11")
        else ()
            set(_std "c99")
        endif ()
        set(_lang "c")
    endif ()
    set(${OUT_STD} "${_std}" PARENT_SCOPE)
    set(${OUT_LANG} "${_lang}" PARENT_SCOPE)
endfunction()

if (P101_CPPCHECK_ARGS STREQUAL "")
    _p101_cppcheck_std(_CPPCHECK_STD _CPPCHECK_LANG)
    set(P101_CPPCHECK_ARGS_LIST ${P101_CPPCHECK_BASE_ARGS})
    list(APPEND P101_CPPCHECK_ARGS_LIST --language=${_CPPCHECK_LANG} --std=${_CPPCHECK_STD})

    # P101_CPPCHECK_EXHAUSTIVE_ARG only exists in cppcheck >= 2.11. On an older
    # cppcheck the option is rejected; passing it (or, worse, silently getting
    # the "normalCheckLevelMaxBranches" advisory when it IS absent) breaks the
    # build. CPU is a poor proxy for the cppcheck version — an old Raspberry Pi
    # ships an old cppcheck, but Apple Silicon (also ARM) ships a modern one —
    # so probe the actual binary: keep the flag only when --help advertises it.
    # Cached so the probe runs once per build tree, not on every reconfigure.
    if (NOT DEFINED P101_CPPCHECK_HAS_CHECK_LEVEL)
        execute_process(
                COMMAND "${CPPCHECK_NAME}" --help
                OUTPUT_VARIABLE _p101_cppcheck_help
                ERROR_VARIABLE _p101_cppcheck_help
                RESULT_VARIABLE _p101_cppcheck_help_rc
        )
        string(FIND "${_p101_cppcheck_help}" "--check-level" _p101_cl_pos)
        if (_p101_cppcheck_help_rc EQUAL 0 AND NOT _p101_cl_pos EQUAL -1)
            set(P101_CPPCHECK_HAS_CHECK_LEVEL ON CACHE INTERNAL "cppcheck supports --check-level")
        else ()
            set(P101_CPPCHECK_HAS_CHECK_LEVEL OFF CACHE INTERNAL "cppcheck supports --check-level")
        endif ()
    endif ()
    if (NOT P101_CPPCHECK_HAS_CHECK_LEVEL)
        list(REMOVE_ITEM P101_CPPCHECK_ARGS_LIST ${P101_CPPCHECK_EXHAUSTIVE_ARG})
    endif ()
endif ()

set(_CPPCHECK_SUPPRESSIONS)
foreach (_s IN LISTS P101_CPPCHECK_SUPPRESSIONS_LIST)
    if (NOT _s STREQUAL "")
        list(APPEND _CPPCHECK_SUPPRESSIONS "--suppress=${_s}")
    endif ()
endforeach ()
if (APPLE AND MAC_SYSROOT)
    list(APPEND _CPPCHECK_SUPPRESSIONS "--suppress=*:${MAC_SYSROOT}/*")
endif ()
list(APPEND _CPPCHECK_SUPPRESSIONS
        "--suppress=*:/usr/include/*"
        "--suppress=*:/usr/local/include/*"
        "--suppress=*:/opt/homebrew/include/*"
        "--suppress=*:/opt/homebrew/opt/*/include/*"
        "--suppress=*:${CMAKE_BINARY_DIR}/*"
        "--relative-paths=${CMAKE_CURRENT_SOURCE_DIR}"
)

# Build cppcheck -I args cleanly (avoid generator expr warnings)
set(_CPPCHECK_I_ARGS)
if (P101_PROJECT_INC_DIR)
    list(APPEND _CPPCHECK_I_ARGS -I "${P101_PROJECT_INC_DIR}")
endif ()
foreach (_d IN LISTS P101_PUBLIC_INCLUDE_DIRS_EXISTING)
    list(APPEND _CPPCHECK_I_ARGS -I "${_d}")
endforeach ()

set(CPPCHECK_LOG "${CMAKE_BINARY_DIR}/cppcheck.log")
set(CPPCHECK_FAIL_SCRIPT "${P101_CMAKE_DIR}/FailIfCppcheckDiagnostics.cmake")

add_custom_target(${PROJECT_NAME}_cppcheck_all ALL
        COMMAND ${CMAKE_COMMAND} -E echo "${ICON} running: ${CPPCHECK_NAME} [see log]"
        COMMAND "${CPPCHECK_NAME}" ${P101_CPPCHECK_ARGS_LIST}
        ${_CPPCHECK_SUPPRESSIONS}
        --template=gcc
        --output-file=${CPPCHECK_LOG}
        --project=${CMAKE_BINARY_DIR}/compile_commands.json
        ${_CPPCHECK_I_ARGS}
        COMMAND ${CMAKE_COMMAND} -DLOGFILE=${CPPCHECK_LOG} -P "${CPPCHECK_FAIL_SCRIPT}"
        USES_TERMINAL
        COMMENT "cppcheck over entire project, fail on any diagnostics")

# ENFORCE: cppcheck after tidy (and thus after analyze/compile/format)
if (P101_PRIMARY_TARGETS)
    add_dependencies(${PROJECT_NAME}_cppcheck_all ${LIBRARY_TARGETS} ${EXECUTABLE_TARGETS})
endif ()
add_dependencies(${PROJECT_NAME}_cppcheck_all ${PROJECT_NAME}_tidy_all)

# =========================
# FINAL: clang static analyzer (--analyze) after cppcheck (Clang only)
#       DO NOT RUN ON RASPBERRY PI (native Linux detection)
# =========================
set(P101_IS_RASPBERRY_PI FALSE)

# Reliable only for native builds on Linux (avoid mis-detecting when cross-compiling).
if (CMAKE_SYSTEM_NAME STREQUAL "Linux" AND NOT CMAKE_CROSSCOMPILING)

    # Preferred: device-tree model string
    if (EXISTS "/proc/device-tree/model")
        file(READ "/proc/device-tree/model" _p101_pi_model)
        string(STRIP "${_p101_pi_model}" _p101_pi_model)
        if (_p101_pi_model MATCHES "Raspberry Pi")
            set(P101_IS_RASPBERRY_PI TRUE)
        endif ()
    endif ()

    # Fallback: cpuinfo markers (less reliable, but helps on some setups)
    if (NOT P101_IS_RASPBERRY_PI AND EXISTS "/proc/cpuinfo")
        file(READ "/proc/cpuinfo" _p101_cpuinfo)
        if (_p101_cpuinfo MATCHES "BCM2708|BCM2709|BCM2710|BCM2711|BCM2712|Raspberry Pi")
            set(P101_IS_RASPBERRY_PI TRUE)
        endif ()
    endif ()

endif ()

if (P101_IS_RASPBERRY_PI)
    message(STATUS "Detected Raspberry Pi; skipping Clang Static Analyzer (--analyze).")
endif ()

if (_COMPILER_ID MATCHES "Clang" AND NOT P101_IS_RASPBERRY_PI)
    set(P101_CLANG_SA_DIR "${CMAKE_BINARY_DIR}/clang-sa")
    file(MAKE_DIRECTORY "${P101_CLANG_SA_DIR}")

    # std flag for clang --analyze — same source of truth as the compiler
    set(_SA_STD "")
    if (NOT P101_STD_FLAG STREQUAL "")
        set(_SA_STD "${P101_STD_FLAG}")
    elseif (PROJECT_LANGUAGE STREQUAL "CXX")
        if (CMAKE_CXX_STANDARD)
            set(_SA_STD "-std=c++${CMAKE_CXX_STANDARD}")
        else ()
            set(_SA_STD "-std=c++17")
        endif ()
    else ()
        if (CMAKE_C_STANDARD)
            if (CMAKE_C_STANDARD GREATER_EQUAL 23)
                set(_SA_STD "-std=c23")
            elseif (CMAKE_C_STANDARD GREATER_EQUAL 17)
                set(_SA_STD "-std=c17")
            elseif (CMAKE_C_STANDARD GREATER_EQUAL 11)
                set(_SA_STD "-std=c11")
            else ()
                set(_SA_STD "-std=c99")
            endif ()
        else ()
            set(_SA_STD "-std=c17")
        endif ()
    endif ()

    # Reuse analyzer-only flags (best-effort; CSA is separate from the syntax-only stage)
    set(_SA_FLAGS ${P101_ANALYZER_ONLY_CFLAGS})

    set(_SA_ARGS_COMMON ${P101_CLANG_SA_COMMON_ARGS})

    # Only analyze implementation files; report only in the main source file
    set(_SA_ARGS_PROFILE ${P101_CLANG_SA_PROFILE_ARGS})

    set(_SA_CHECKER_GROUPS ${P101_CLANG_SA_BASE_CHECKERS})
    if (APPLE)
        list(APPEND _SA_CHECKER_GROUPS ${P101_CLANG_SA_APPLE_CHECKERS})
    endif ()

    if (NOT _COMPILER_ID STREQUAL "AppleClang")
        list(APPEND _SA_CHECKER_GROUPS ${P101_CLANG_SA_NON_APPLE_CHECKERS})
        if (PROJECT_LANGUAGE STREQUAL "CXX")
            list(APPEND _SA_CHECKER_GROUPS ${P101_CLANG_SA_CXX_CHECKERS})
        endif ()
        if (P101_CLANG_SA_PROFILE STREQUAL "deep")
            list(APPEND _SA_CHECKER_GROUPS ${P101_CLANG_SA_DEEP_CHECKERS})
            list(APPEND _SA_ARGS_PROFILE ${P101_CLANG_SA_DEEP_ARGS})
        endif ()
    endif ()

    foreach (_grp IN LISTS _SA_CHECKER_GROUPS)
        list(APPEND _SA_ARGS_PROFILE -Xanalyzer -analyzer-checker=${_grp})
    endforeach ()

    set(_CSA_DISABLE_LIST ${P101_CLANG_SA_DISABLE_CHECKERS})
    if ("${P101_CLANG_SA_DISABLE_CHECKERS}" MATCHES ",")
        string(REPLACE "," ";" _CSA_DISABLE_LIST "${P101_CLANG_SA_DISABLE_CHECKERS}")
    endif ()
    foreach (_dis IN LISTS _CSA_DISABLE_LIST)
        string(STRIP "${_dis}" _dis)
        if (NOT _dis STREQUAL "")
            list(APPEND _SA_ARGS_PROFILE -Xanalyzer -analyzer-disable-checker=${_dis})
        endif ()
    endforeach ()

    set(_SA_ARGS ${_SA_ARGS_COMMON} ${_SA_ARGS_PROFILE})
    if (P101_CLANG_SA_EXTRA_ARGS_LIST)
        list(APPEND _SA_ARGS ${P101_CLANG_SA_EXTRA_ARGS_LIST})
    endif ()

    string(JOIN " " _SA_ARGS_CMD ${_SA_ARGS})
    string(JOIN " " _SA_FLAGS_CMD ${_SA_FLAGS})

    set(_P101_SA_DEFS ${P101_FEATURE_TEST_FLAGS})

    string(JOIN " " _P101_SA_DEFS_CMD ${_P101_SA_DEFS})

    set(_SA_STAMPS)
    foreach (_abs IN LISTS _ALL_SOURCES_FOR_FORMAT)
        string(FIND "${_abs}" "${CMAKE_CURRENT_SOURCE_DIR}/" _pfx)
        if (NOT _pfx EQUAL 0)
            message(FATAL_ERROR "Source '${_abs}' is outside the project directory "
                    "(${CMAKE_CURRENT_SOURCE_DIR}). All sources must be in-tree; an "
                    "out-of-tree path almost always indicates a config.cmake error.")
        endif ()
        file(RELATIVE_PATH _rel "${CMAKE_CURRENT_SOURCE_DIR}" "${_abs}")
        string(REPLACE "/" "_" _flat "${_rel}")
        set(_out "${P101_CLANG_SA_DIR}/${_flat}.txt")
        set(_stamp "${P101_CLANG_SA_DIR}/${_flat}.stamp")

        add_custom_command(
                OUTPUT "${_stamp}"
                COMMAND ${CMAKE_COMMAND} -E echo
                "${ICON} running: ${_COMPILER} ${_SA_STD} ${_P101_SA_DEFS_CMD} ${_SA_FLAGS_CMD} ${_SA_ARGS_CMD} ${_PROJ_I_CMD} ${_LOCAL_I_CMD} ${_EXT_ISYSTEM_CMD} ${_MAC_SYSROOT_CMD} \"${_abs}\""
                COMMAND /bin/sh -c
                "\"${_COMPILER}\" ${_SA_STD} ${_P101_SA_DEFS_CMD} ${_SA_FLAGS_CMD} ${_SA_ARGS_CMD} \
                   ${_PROJ_I_CMD} ${_LOCAL_I_CMD} ${_EXT_ISYSTEM_CMD} ${_MAC_SYSROOT_CMD} \
                   \"${_abs}\" > \"${_out}\" 2>&1"
                COMMAND ${CMAKE_COMMAND} -E touch "${_stamp}"
                DEPENDS "${_abs}"
                IMPLICIT_DEPENDS C "${_abs}"
                VERBATIM
        )
        list(APPEND _SA_STAMPS "${_stamp}")
    endforeach ()

    set(CLANG_SA_FAIL_SCRIPT "${P101_CMAKE_DIR}/FailIfClangSADiagnostics.cmake")

    if (_SA_STAMPS)
        add_custom_target(${PROJECT_NAME}_clang_sa_all ALL
                DEPENDS ${_SA_STAMPS}
                COMMAND ${CMAKE_COMMAND} -E env P101_CLANG_SA_FAIL=$<IF:$<BOOL:${P101_CLANG_SA_FAIL_ON_DIAGNOSTICS}>,1,0>
                ${CMAKE_COMMAND} -DDIR=${P101_CLANG_SA_DIR} -P "${CLANG_SA_FAIL_SCRIPT}"
                COMMENT "Clang static analyzer (--analyze) over project sources (in-depth)")
        add_dependencies(${PROJECT_NAME}_clang_sa_all ${PROJECT_NAME}_cppcheck_all)
    else ()
        add_custom_target(${PROJECT_NAME}_clang_sa_all ALL
                COMMAND ${CMAKE_COMMAND} -E echo "No sources for clang --analyze"
                COMMENT "Clang static analyzer (--analyze)")
        add_dependencies(${PROJECT_NAME}_clang_sa_all ${PROJECT_NAME}_cppcheck_all)
    endif ()

    # ---- OPTIONAL: cross-translation-unit (CTU) analysis ----
    # Per-file --analyze can't see a bug that only appears across a call
    # boundary (e.g. one TU passing 0 into a divide defined in another). CTU
    # can, but it needs clang's `clang-extdef-mapping` companion, which ships
    # next to a mainline/Homebrew LLVM clang but NOT Apple's stock clang.
    # Strictly best-effort: engage ONLY when the build compiler has a matching
    # clang-extdef-mapping sibling (AST format + flags stay consistent — never
    # mix compilers). If it isn't there, we skip silently and the per-file CSA
    # above is the analysis — "do the best with the tools this machine has".
    set(P101_CLANG_SA_CTU ON CACHE BOOL "Run cross-TU analysis when clang-extdef-mapping is available")
    if (P101_CLANG_SA_CTU AND _SA_STAMPS)
        get_filename_component(_CC_REAL "${_COMPILER}" REALPATH)
        get_filename_component(_CC_REAL_DIR "${_CC_REAL}" DIRECTORY)
        get_filename_component(_CC_LINK_DIR "${_COMPILER}" DIRECTORY)
        set(_CC_MAJOR "")
        if (PROJECT_LANGUAGE STREQUAL "CXX" AND CMAKE_CXX_COMPILER_VERSION)
            string(REGEX MATCH "^[0-9]+" _CC_MAJOR "${CMAKE_CXX_COMPILER_VERSION}")
        elseif (CMAKE_C_COMPILER_VERSION)
            string(REGEX MATCH "^[0-9]+" _CC_MAJOR "${CMAKE_C_COMPILER_VERSION}")
        endif ()
        # resolved bin dir carries the unversioned tool (llvm-NN/bin, Homebrew);
        # the symlink dir may carry a versioned one (/usr/bin/...-18 on Debian)
        set(P101_EXTDEF_MAPPING "")
        foreach (_c
                "${_CC_REAL_DIR}/clang-extdef-mapping"
                "${_CC_LINK_DIR}/clang-extdef-mapping"
                "${_CC_LINK_DIR}/clang-extdef-mapping-${_CC_MAJOR}"
                "${_CC_REAL_DIR}/clang-extdef-mapping-${_CC_MAJOR}")
            if (EXISTS "${_c}")
                set(P101_EXTDEF_MAPPING "${_c}")
                break ()
            endif ()
        endforeach ()

        if (P101_EXTDEF_MAPPING)
            # same checker/config profile as the per-file pass, minus the bare
            # --analyze (the helper adds it); it also adds the CTU config
            set(_CTU_ARGS ${P101_CLANG_SA_CTU_COMMON_ARGS} ${_SA_ARGS_PROFILE})
            if (P101_CLANG_SA_EXTRA_ARGS_LIST)
                list(APPEND _CTU_ARGS ${P101_CLANG_SA_EXTRA_ARGS_LIST})
            endif ()
            add_custom_target(${PROJECT_NAME}_clang_sa_ctu_all ALL
                    COMMAND ${CMAKE_COMMAND} -E echo "${ICON} clang SA cross-TU (CTU) analysis (extdef-mapping: ${P101_EXTDEF_MAPPING})"
                    COMMAND "${PYTHON3_EXECUTABLE}" "${P101_CMAKE_DIR}/RunClangSaCtu.py"
                            "${_COMPILER}" "${P101_EXTDEF_MAPPING}"
                            "${CMAKE_BINARY_DIR}/compile_commands.json"
                            "${CMAKE_BINARY_DIR}/clang-sa-ctu"
                            "$<IF:$<BOOL:${P101_CLANG_SA_FAIL_ON_DIAGNOSTICS}>,1,0>"
                            "${CMAKE_CURRENT_SOURCE_DIR}"
                            -- ${_CTU_ARGS}
                    USES_TERMINAL
                    VERBATIM
                    COMMENT "Clang static analyzer, cross-translation-unit (interprocedural across files)")
            add_dependencies(${PROJECT_NAME}_clang_sa_ctu_all ${PROJECT_NAME}_clang_sa_all)
        else ()
            message(STATUS "clang-extdef-mapping not found for ${_COMPILER}; skipping optional cross-TU (CTU) analysis.")
        endif ()
    endif ()
elseif (_COMPILER_ID MATCHES "Clang" AND P101_IS_RASPBERRY_PI)
    message(STATUS "Skipping Clang Static Analyzer (--analyze) on Raspberry Pi")
endif ()


# Installation and the configure receipt are separate policy modules. Keeping
# them out of this orchestration file makes their inputs and ownership clear.
include("${P101_CMAKE_DIR}/P101Install.cmake")
include("${P101_CMAKE_DIR}/P101Summary.cmake")
