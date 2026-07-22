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

project(${PROJECT_NAME}
        VERSION ${PROJECT_VERSION}
        DESCRIPTION "${PROJECT_DESCRIPTION}"
        LANGUAGES ${PROJECT_LANGUAGE})

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

# Python (required for JSON-safe compile DB sanitizer + analyze-from-DB)
find_program(PYTHON3_EXECUTABLE NAMES python3 python REQUIRED DOC "Python executable for helper scripts")

# Sanitizers requested (comma-separated names) -> read matching *_sanitizer_flags.txt
set(SANITIZER_LIST "" CACHE STRING "Comma-separated sanitizers to try to enable")

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

set(CPPCHECK_SUPPRESSIONS
        "missingIncludeSystem unusedFunction staticFunction constParameterPointer unmatchedSuppression checkersReport"
        CACHE STRING "Space-separated cppcheck suppressions"
)

set(P101_ANALYZE_FAIL_ON_DIAGNOSTICS OFF CACHE BOOL "Fail build if analyze stage emits diagnostics")

# Clang Static Analyzer deep pass
set(P101_CLANG_SA_PROFILE "deep" CACHE STRING "CSA profile: basic|deep")
set_property(CACHE P101_CLANG_SA_PROFILE PROPERTY STRINGS basic deep)
set(P101_CLANG_SA_FAIL_ON_DIAGNOSTICS ON CACHE BOOL "Fail build on any CSA diagnostic")
set(P101_CLANG_SA_EXTRA_ARGS "" CACHE STRING "Extra --analyze args for clang (space-separated)")

# Disable noisy checker
set(P101_CLANG_SA_DISABLE_CHECKERS
        "alpha.core.Conversion,security.insecureAPI.DeprecatedOrUnsafeBufferHandling,security.insecureAPI.strcpy"
        CACHE STRING "Comma-separated Clang Static Analyzer checkers to disable")

# =========================
# QoL
# =========================
set(CMAKE_EXPORT_COMPILE_COMMANDS ON CACHE BOOL "Export compile_commands.json" FORCE)
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
            "workspace run scripts/link-cmake.sh to symlink them; for a "
            "standalone checkout run scripts/recreate-cmake-helpers.sh.")
endif ()

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
separate_arguments(P101_TIDY_EXTRA_ARGS_LIST NATIVE_COMMAND "${P101_TIDY_EXTRA_ARGS}")
separate_arguments(P101_CPPCHECK_ARGS_LIST NATIVE_COMMAND "${P101_CPPCHECK_ARGS}")
separate_arguments(CPPCHECK_SUPPRESSIONS_LIST NATIVE_COMMAND "${CPPCHECK_SUPPRESSIONS}")
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
set(MAC_SYSROOT "")
if (APPLE)
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
    message(STATUS "No flags dir for ${_CC_BASE}; no compiler flags will be loaded.")
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
        endif ()
    endforeach ()
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

set(_INC_CANDIDATES
        "${PUBLIC_INC_DIR}"
        "/usr/local/include"
        ${P101_PUBLIC_INCLUDE_DIRS_LIST}
)
set(_LIB_CANDIDATES
        "/usr/local/lib" "/usr/local/lib64"
        ${P101_PUBLIC_LINK_DIRS_LIST}
)
if (APPLE)
    list(APPEND _INC_CANDIDATES /opt/homebrew/include /opt/local/include)
    list(APPEND _LIB_CANDIDATES /opt/homebrew/lib /opt/local/lib)
endif ()
list(REMOVE_DUPLICATES _INC_CANDIDATES)
list(REMOVE_DUPLICATES _LIB_CANDIDATES)

_p101_filter_existing_dirs(P101_PUBLIC_INCLUDE_DIRS_EXISTING ${_INC_CANDIDATES})
_p101_filter_existing_dirs(P101_PUBLIC_LINK_DIRS_EXISTING ${_LIB_CANDIDATES})

if (P101_PUBLIC_LINK_DIRS_EXISTING)
    set(CMAKE_BUILD_RPATH "${P101_PUBLIC_LINK_DIRS_EXISTING}")
    set(CMAKE_INSTALL_RPATH "${P101_PUBLIC_LINK_DIRS_EXISTING}")
    set(CMAKE_INSTALL_RPATH_USE_LINK_PATH ON)
endif ()

message(STATUS "Public include dirs used: ${P101_PUBLIC_INCLUDE_DIRS_EXISTING}")
message(STATUS "Public link dirs used:    ${P101_PUBLIC_LINK_DIRS_EXISTING}")

# --- Classify include dirs into project vs external (system)
set(P101_PROJECT_INC_DIR "")
if (IS_DIRECTORY "${PUBLIC_INC_DIR}")
    set(P101_PROJECT_INC_DIR "${PUBLIC_INC_DIR}")
endif ()

set(_EXT_INC_DIRS "${P101_PUBLIC_INCLUDE_DIRS_EXISTING}")
if (P101_PROJECT_INC_DIR)
    list(REMOVE_ITEM _EXT_INC_DIRS "${P101_PROJECT_INC_DIR}")
endif ()
list(REMOVE_DUPLICATES _EXT_INC_DIRS)

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

# =========================
# Iconv (portable, **opt-in via "iconv" token in config.cmake**)
# =========================
# Always provide an interface target; it will add a real lib only where needed.
if(NOT TARGET p101::iconv)
    add_library(p101::iconv INTERFACE IMPORTED)
endif()

# Try to locate a concrete libiconv first (BSD/macOS/Homebrew/MacPorts, etc.)
find_library(P101_ICONV_LIB
        NAMES iconv libiconv
        HINTS ${P101_PUBLIC_LINK_DIRS_EXISTING}
        /usr/local/lib /usr/lib /opt/homebrew/lib /opt/local/lib
)

if(P101_ICONV_LIB)
    set_property(TARGET p101::iconv PROPERTY INTERFACE_LINK_LIBRARIES "${P101_ICONV_LIB}")
    message(STATUS "[iconv] using concrete library: ${P101_ICONV_LIB}")
elseif(CMAKE_SYSTEM_NAME MATCHES "FreeBSD|DragonFly|OpenBSD|NetBSD|Darwin")
    # Best-effort: request -liconv (on some platforms it may be folded into libc).
    set_property(TARGET p101::iconv PROPERTY INTERFACE_LINK_LIBRARIES "iconv")
    message(STATUS "[iconv] linking as -liconv on ${CMAKE_SYSTEM_NAME}")
else()
    message(STATUS "[iconv] no separate lib needed on this platform")
endif()

# Resolve link items from config.cmake:
# - "iconv" token -> p101::iconv
# - targets stay targets
# - absolute existing paths stay as-is
# - "-lfoo" / "foo" are resolved via find_library in known roots when possible
# - unknown items are preserved as-is (e.g., linker flags)
function(_p101_resolve_link_items OUT)
    set(_accum "")

    foreach(_L IN LISTS ARGN)
        if(_L STREQUAL "")
            continue()
        endif()

        if(_L STREQUAL "iconv")
            list(APPEND _accum p101::iconv)
            continue()
        endif()

        if(TARGET "${_L}")
            list(APPEND _accum "${_L}")
            continue()
        endif()

        if(IS_ABSOLUTE "${_L}" AND EXISTS "${_L}")
            list(APPEND _accum "${_L}")
            continue()
        endif()

        set(_name "${_L}")
        if(_name MATCHES "^-l(.+)$")
            set(_name "${CMAKE_MATCH_1}")
        endif()

        find_library(_FOUND_LIB
                NAMES "${_name}" "lib${_name}"
                HINTS ${P101_PUBLIC_LINK_DIRS_EXISTING}
                /usr/local/lib /usr/local/lib64 /usr/lib /usr/lib64
                /opt/homebrew/lib /opt/local/lib
        )

        if(_FOUND_LIB)
            list(APPEND _accum "${_FOUND_LIB}")
        else()
            list(APPEND _accum "${_L}")
        endif()

        unset(_FOUND_LIB CACHE)
        unset(_FOUND_LIB)
    endforeach()

    list(REMOVE_DUPLICATES _accum)
    set(${OUT} "${_accum}" PARENT_SCOPE)
endfunction()

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
    # External as SYSTEM (-isystem)
    if (_EXT_INC_DIRS)
        target_include_directories(${_lib} SYSTEM PUBLIC ${_EXT_INC_DIRS})
    endif ()

    target_compile_options(${_lib} PRIVATE ${STANDARD_FLAGS} ${P101_EXTRA_CFLAGS} ${_P101_SANITIZER_COMPILE_OPTS})
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

    # Project includes as normal (-I)
    if (P101_PROJECT_INC_DIR)
        target_include_directories(${_exe} PRIVATE "${P101_PROJECT_INC_DIR}")
    endif ()
    # External as SYSTEM (-isystem)
    if (_EXT_INC_DIRS)
        target_include_directories(${_exe} SYSTEM PRIVATE ${_EXT_INC_DIRS})
    endif ()

    target_compile_options(${_exe} PRIVATE ${STANDARD_FLAGS} ${P101_EXTRA_CFLAGS} ${_P101_SANITIZER_COMPILE_OPTS})
    target_link_options(${_exe} PRIVATE ${P101_EXTRA_LDFLAGS} ${_P101_SANITIZER_LINK_OPTS})

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
    execute_process(
            COMMAND ${_COMPILER} --print-resource-dir
            OUTPUT_VARIABLE _CLANG_RESOURCE_DIR
            OUTPUT_STRIP_TRAILING_WHITESPACE
    )

    set(_ASAN_RPATH "${_CLANG_RESOURCE_DIR}/lib/darwin")

    if(IS_DIRECTORY "${_ASAN_RPATH}")
        foreach(_tgt IN LISTS LIBRARY_TARGETS EXECUTABLE_TARGETS)
            # APPEND: the targets already inherited /usr/local/lib etc. from
            # CMAKE_BUILD_RPATH/CMAKE_INSTALL_RPATH — replacing those would
            # break @rpath resolution of the installed p101 dylibs on macOS.
            set_property(TARGET ${_tgt} APPEND PROPERTY BUILD_RPATH "${_ASAN_RPATH}")
            set_property(TARGET ${_tgt} APPEND PROPERTY INSTALL_RPATH "${_ASAN_RPATH}")
        endforeach()
        message(STATUS "ASan rpath added: ${_ASAN_RPATH}")
    else()
        message(STATUS "ASan rpath dir not found: ${_ASAN_RPATH}")
    endif()
endif()

# =========================
# clang-format (format-before-compile)  [INTENTIONALLY KEPT]
# =========================
if (CLANG_FORMAT_NAME AND _ALL_SOURCES_FOR_FORMAT)
    set(_FORMAT_STAMPS)
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
        set(_stamp "${CMAKE_BINARY_DIR}/.format/${_rel}.stamp")
        # Intentional: -i REWRITES the source in place (auto-format on every
        # build), not a --dry-run check. Requires a writable source tree.
        add_custom_command(
                OUTPUT "${_stamp}"
                COMMAND ${CMAKE_COMMAND} -E make_directory "${CMAKE_BINARY_DIR}/.format/${_rel_dir}"
                COMMAND ${CMAKE_COMMAND} -E echo "${ICON} running: ${CLANG_FORMAT_NAME} -i -style=file ${_abs}"
                COMMAND "${CLANG_FORMAT_NAME}" -i -style=file "${_abs}"
                COMMAND ${CMAKE_COMMAND} -E touch "${_stamp}"
                DEPENDS "${_abs}"
                VERBATIM
        )
        list(APPEND _FORMAT_STAMPS "${_stamp}")
    endforeach ()
    if (_FORMAT_STAMPS)
        add_custom_target(${PROJECT_NAME}_format_all DEPENDS ${_FORMAT_STAMPS})
        foreach (_tgt IN LISTS LIBRARY_TARGETS EXECUTABLE_TARGETS)
            add_dependencies(${_tgt} ${PROJECT_NAME}_format_all)
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
    set(P101_TIDY_CHECKS_LIST
            "*"
            "-llvmlibc-*"
            "-clang-diagnostic-unused-macros"
            "-llvmlibc-restrict-system-libc-headers"
            "-altera-struct-pack-align"
            "-readability-identifier-length"
            "-altera-unroll-loops"
            "-cppcoreguidelines-init-variables"
            "-cert-err33-c"
            "-bugprone-easily-swappable-parameters"
            "-clang-analyzer-security.insecureAPI.DeprecatedOrUnsafeBufferHandling"
            "-altera-id-dependent-backward-branch"
            "-concurrency-mt-unsafe"
            "-misc-unused-parameters"
            "-hicpp-signed-bitwise"
            "-google-readability-todo"
            "-cert-msc30-c"
            "-readability-function-cognitive-complexity"
            "-clang-analyzer-security.insecureAPI.strcpy"
            "-cert-env33-c"
            "-android-cloexec-accept"
            "-misc-include-cleaner"
            "-llvm-header-guard"
            "-google-readability-casting"
            "-readability-redundant-casting"
            # fuchsia-trailing-return directly contradicts
            # modernize-use-trailing-return-type under checks=* — with
            # -warnings-as-errors=* NO C++ function could pass the gate.
            # Keep the modernize (trailing-return) style; drop the fuchsia ban.
            "-fuchsia-trailing-return"
    )
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
        "-warnings-as-errors=*"
        "-quiet"
        "--format-style=file"
        "--extra-arg-before=-D_POSIX_C_SOURCE=200809L"
        "--extra-arg-before=-D_XOPEN_SOURCE=700"
)

# _GNU_SOURCE is a glibc feature-test macro. "UNIX AND NOT APPLE" also matches
# the BSDs, where it is not meaningful and can perturb declaration visibility;
# BSD gets __BSD_VISIBLE below instead. Gate on Linux specifically.
if (CMAKE_SYSTEM_NAME STREQUAL "Linux")
    list(APPEND _P101_TIDY_ARGS "--extra-arg-before=-D_GNU_SOURCE")
endif()

if (APPLE)
    list(APPEND _P101_TIDY_ARGS "--extra-arg-before=-D_DARWIN_C_SOURCE")
endif()

# BSD visibility for clang-tidy too
if (CMAKE_SYSTEM_NAME MATCHES "FreeBSD|DragonFly|OpenBSD|NetBSD")
    list(APPEND _P101_TIDY_ARGS "--extra-arg-before=-D__BSD_VISIBLE=1")
endif ()

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
    set(P101_CPPCHECK_ARGS_LIST
            --enable=all --inconclusive --check-level=exhaustive --library=posix
            --force --inline-suppr --quiet
            --language=${_CPPCHECK_LANG} --std=${_CPPCHECK_STD}
    )

    # --check-level=exhaustive only exists in cppcheck >= 2.11. On an older
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
        list(REMOVE_ITEM P101_CPPCHECK_ARGS_LIST --check-level=exhaustive)
    endif ()
endif ()

set(_CPPCHECK_SUPPRESSIONS)
foreach (_s IN LISTS CPPCHECK_SUPPRESSIONS_LIST)
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

    set(_SA_ARGS_COMMON
            --analyze
            -Xanalyzer -analyzer-output=text
    )

    # Only analyze implementation files; report only in the main source file
    set(_SA_ARGS_PROFILE
            -Xanalyzer -analyzer-config -Xanalyzer analyze-headers=false
            -Xanalyzer -analyzer-config -Xanalyzer report-in-main-source-file=true
    )

    set(_SA_CHECKER_GROUPS core unix security nullability)
    if (APPLE)
        list(APPEND _SA_CHECKER_GROUPS osx)
    endif ()

    if (NOT _COMPILER_ID STREQUAL "AppleClang")
        list(APPEND _SA_CHECKER_GROUPS deadcode optin apiModeling)
        if (PROJECT_LANGUAGE STREQUAL "CXX")
            list(APPEND _SA_CHECKER_GROUPS cplusplus)
        endif ()
        if (P101_CLANG_SA_PROFILE STREQUAL "deep")
            list(APPEND _SA_CHECKER_GROUPS alpha.core alpha.security alpha.unix)
            list(APPEND _SA_ARGS_PROFILE
                    -Xanalyzer -analyzer-config -Xanalyzer aggressive-binary-operation-simplification=true
                    -Xanalyzer -analyzer-config -Xanalyzer unroll-loops=true
            )
        endif ()
    endif ()

    foreach (_grp IN LISTS _SA_CHECKER_GROUPS)
        list(APPEND _SA_ARGS_PROFILE -Xanalyzer -analyzer-checker=${_grp})
    endforeach ()

    string(REPLACE "," ";" _CSA_DISABLE_LIST "${P101_CLANG_SA_DISABLE_CHECKERS}")
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

    set(_P101_SA_DEFS
            -D_POSIX_C_SOURCE=200809L
            -D_XOPEN_SOURCE=700
    )

    if (APPLE)
        list(APPEND _P101_SA_DEFS -D_DARWIN_C_SOURCE)
    elseif (CMAKE_SYSTEM_NAME STREQUAL "Linux")
        # _GNU_SOURCE is glibc-specific; BSD (also UNIX) gets __BSD_VISIBLE below.
        list(APPEND _P101_SA_DEFS -D_GNU_SOURCE)
    endif()

    if (CMAKE_SYSTEM_NAME MATCHES "FreeBSD|DragonFly|OpenBSD|NetBSD")
        list(APPEND _P101_SA_DEFS -D__BSD_VISIBLE=1)
    endif ()

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
                "${ICON} running: ${_COMPILER} ${_SA_STD} ${_P101_SA_DEFS_CMD} ${_SA_FLAGS_CMD} ${_SA_ARGS_CMD} ${_PROJ_I_CMD} ${_EXT_ISYSTEM_CMD} ${_MAC_SYSROOT_CMD} \"${_abs}\""
                COMMAND /bin/sh -c
                "\"${_COMPILER}\" ${_SA_STD} ${_P101_SA_DEFS_CMD} ${_SA_FLAGS_CMD} ${_SA_ARGS_CMD} \
                   ${_PROJ_I_CMD} ${_EXT_ISYSTEM_CMD} ${_MAC_SYSROOT_CMD} \
                   \"${_abs}\" > \"${_out}\" 2>&1 || true"
                COMMAND ${CMAKE_COMMAND} -E touch "${_stamp}"
                DEPENDS "${_abs}"
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
            set(_CTU_ARGS -Xanalyzer -analyzer-output=text ${_SA_ARGS_PROFILE})
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


# =========================
# Install
# =========================
if (_ALL_HEADERS_FOR_INSTALL)
    foreach (_h IN LISTS _ALL_HEADERS_FOR_INSTALL)
        if (NOT IS_ABSOLUTE "${_h}")
            set(_habspath "${CMAKE_CURRENT_SOURCE_DIR}/${_h}")
        else ()
            set(_habspath "${_h}")
        endif ()
        string(FIND "${_habspath}" "${PUBLIC_INC_DIR}/" _pfx)
        if (_pfx EQUAL 0)
            file(RELATIVE_PATH _rel_inside_inc "${PUBLIC_INC_DIR}" "${_habspath}")
            get_filename_component(_dest_subdir "${_rel_inside_inc}" DIRECTORY)
        else ()
            get_filename_component(_bn "${_habspath}" NAME)
            set(_dest_subdir "extra")
        endif ()
        install(FILES "${_habspath}" DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/${_dest_subdir}")
    endforeach ()
endif ()

foreach (_lib IN LISTS LIBRARY_TARGETS)
    install(TARGETS ${_lib}
            LIBRARY DESTINATION "${CMAKE_INSTALL_LIBDIR}"
            ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}"
            RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}")
endforeach ()

foreach (_exe IN LISTS EXECUTABLE_TARGETS)
    install(TARGETS ${_exe}
            RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}")
endforeach ()

# =========================
# Summary banner
# =========================
message(STATUS "")
message(STATUS "-- Configuring with:")
if (DEFINED CMAKE_C_COMPILER)
    message(STATUS "--   CC               = ${CMAKE_C_COMPILER}")
endif ()
if (DEFINED CMAKE_CXX_COMPILER)
    message(STATUS "--   CXX              = ${CMAKE_CXX_COMPILER}")
endif ()
message(STATUS "--   clang-format     = ${CLANG_FORMAT_NAME}")
message(STATUS "--   clang-tidy       = ${CLANG_TIDY_NAME} (custom target over sanitized DB)")
message(STATUS "--   cppcheck         = ${CPPCHECK_NAME} (REQUIRED)")
message(STATUS "--   scan-build       = ${SCAN_BUILD_NAME} (optional target)")
message(STATUS "--   sanitizers       = ${SANITIZER_LIST}")
message(STATUS "--   analyze fail?    = ${P101_ANALYZE_FAIL_ON_DIAGNOSTICS}")
message(STATUS "--   CSA profile      = ${P101_CLANG_SA_PROFILE}")
message(STATUS "--   CSA disabled     = ${P101_CLANG_SA_DISABLE_CHECKERS}")
message(STATUS "--   CSA fail?        = ${P101_CLANG_SA_FAIL_ON_DIAGNOSTICS}")
if (APPLE)
    message(STATUS "--   macOS SDK        = ${MAC_SYSROOT}")
endif ()
message(STATUS "--   build dir        = ${CMAKE_BINARY_DIR}")
message(STATUS "")
