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

# Do not use CMake’s built-in tidy/cppcheck launchers
set(CMAKE_C_CLANG_TIDY "" CACHE STRING "" FORCE)
set(CMAKE_CXX_CLANG_TIDY "" CACHE STRING "" FORCE)
set(CMAKE_C_CPPCHECK "" CACHE STRING "" FORCE)
set(CMAKE_CXX_CPPCHECK "" CACHE STRING "" FORCE)

# Echo compile/link commands
set(_LAUNCH_CMD "echo \"${ICON} compiling/linking: $*\" && exec \"$@\"")
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
set(_FLAGS_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.flags")

set(_FLAG_DIRS_USED "")
if (EXISTS "${_FLAGS_ROOT}/${_CC_BASE}")
    set(_FLAG_DIRS_USED "${_FLAGS_ROOT}/${_CC_BASE}")
else ()
    message(STATUS "No flags dir for ${_CC_BASE}; no compiler flags will be loaded.")
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
    foreach (_ff IN ITEMS
            analyzer_flags.txt
            code_generation_flags.txt
            debug_flags.txt
            optimization_flags.txt
            warning_flags.txt
            instrumentation_flags.txt
            instrumentation_compiler.txt
    )
        set(_p "${_FLAG_DIRS_USED}/${_ff}")
        _p101_read_flags("${_p}" _f)
        if (NOT _f)
            continue()
        endif ()

        if (_ff STREQUAL "analyzer_flags.txt")
            list(APPEND P101_ANALYZER_ONLY_CFLAGS ${_f})
        elseif (_ff MATCHES "instrumentation_compiler")
            list(APPEND P101_EXTRA_CFLAGS ${_f})
        elseif (_ff MATCHES "instrumentation_flags")
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

set(DETECTED_SANITIZERS "${_P101_SANITIZER_COMPILE_OPTS}" CACHE STRING "Effective sanitizer flags")
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
list(FILTER LIBRARY_TARGETS EXCLUDE REGEX "^(|\\s+)$")
list(FILTER EXECUTABLE_TARGETS EXCLUDE REGEX "^(|\\s+)$")

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

    list(APPEND _ALL_HEADERS_FOR_INSTALL ${${_lib}_HEADERS})
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

if(APPLE AND _COMPILER_ID MATCHES "Clang")
    execute_process(
            COMMAND ${_COMPILER} --print-resource-dir
            OUTPUT_VARIABLE _CLANG_RESOURCE_DIR
            OUTPUT_STRIP_TRAILING_WHITESPACE
    )

    set(_ASAN_RPATH "${_CLANG_RESOURCE_DIR}/lib/darwin")

    if(IS_DIRECTORY "${_ASAN_RPATH}")
        foreach(_tgt IN LISTS LIBRARY_TARGETS EXECUTABLE_TARGETS)
            set_property(TARGET ${_tgt} PROPERTY BUILD_RPATH "${_ASAN_RPATH}")
            set_property(TARGET ${_tgt} PROPERTY INSTALL_RPATH "${_ASAN_RPATH}")
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
        if (NOT _abs MATCHES "^${CMAKE_CURRENT_SOURCE_DIR}/")
            continue()
        endif ()
        file(RELATIVE_PATH _rel "${CMAKE_CURRENT_SOURCE_DIR}" "${_abs}")
        get_filename_component(_rel_dir "${_rel}" DIRECTORY)
        set(_stamp "${CMAKE_BINARY_DIR}/.format/${_rel}.stamp")
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

set(SAN_PY "${CMAKE_BINARY_DIR}/SanitizeCompileCommands.py")
file(WRITE "${SAN_PY}" [=[
#!/usr/bin/env python3
import json, os, shlex, sys

DROP_EXACT = {"--coverage", "-coverage", "-pg"}
DROP_PAIR_FLAGS = {"-p"}  # drop -p <arg> if present

def should_drop(tok: str) -> bool:
    if tok in DROP_EXACT:
        return True
    if tok.startswith("-W"):
        return True
    if tok.startswith("-f"):
        return True
    if tok.startswith("-g"):
        return True
    return False

def sanitize_args(argv):
    out = []
    skip_next = False
    for a in argv:
        if skip_next:
            skip_next = False
            continue
        if a in DROP_PAIR_FLAGS:
            skip_next = True
            continue
        if should_drop(a):
            continue
        out.append(a)
    return out

def main():
    if len(sys.argv) != 3:
        print("Usage: SanitizeCompileCommands.py <in.json> <out.json>", file=sys.stderr)
        return 2

    in_path, out_path = sys.argv[1], sys.argv[2]
    with open(in_path, "r", encoding="utf-8") as f:
        db = json.load(f)

    for e in db:
        if "arguments" in e and isinstance(e["arguments"], list):
            argv = e["arguments"]
        else:
            cmd = e.get("command", "")
            argv = shlex.split(cmd)

        argv = sanitize_args(argv)
        e["arguments"] = argv
        if "command" in e:
            del e["command"]

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2)
        f.write("\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
]=])
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
    )
    string(JOIN "," P101_TIDY_CHECKS ${P101_TIDY_CHECKS_LIST})
endif ()

# Default tidy header filter: only our project (src/ and include/) unless user overrides
if (P101_HEADER_FILTER STREQUAL "" OR P101_HEADER_FILTER STREQUAL ".*")
    set(P101_HEADER_FILTER "^(${CMAKE_CURRENT_SOURCE_DIR}/src|${PUBLIC_INC_DIR})/.*")
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

if (UNIX AND NOT APPLE)
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
    if (_abs MATCHES "^${CMAKE_CURRENT_SOURCE_DIR}/")
        list(APPEND _TIDY_FILES "${_abs}")
    endif ()
endforeach ()
file(WRITE "${_TIDY_FILES_CMAKE}" "set(P101_TIDY_FILES_LIST\n")
foreach (_f IN LISTS _TIDY_FILES)
    string(REPLACE "\\" "\\\\" _f_esc "${_f}")
    string(REPLACE "\"" "\\\"" _f_esc "${_f_esc}")
    file(APPEND "${_TIDY_FILES_CMAKE}" "  \"${_f_esc}\"\n")
endforeach ()
file(APPEND "${_TIDY_FILES_CMAKE}" ")\n")

set(RUN_TIDY_SCRIPT "${CMAKE_BINARY_DIR}/RunClangTidyOverList.cmake")
file(WRITE "${RUN_TIDY_SCRIPT}" [=[
cmake_minimum_required(VERSION 3.20)
if(NOT DEFINED CLANG_TIDY_EXEC OR NOT DEFINED DB OR NOT DEFINED FILES_CMAKE)
  message(FATAL_ERROR "Need -DCLANG_TIDY_EXEC=, -DDB=, and -DFILES_CMAKE=")
endif()
if(NOT EXISTS "${FILES_CMAKE}")
  message(FATAL_ERROR "FILES_CMAKE not found: ${FILES_CMAKE}")
endif()
include("${FILES_CMAKE}")

set(_args)
if(DEFINED ARGS_CMAKE AND NOT ARGS_CMAKE STREQUAL "")
  if(NOT EXISTS "${ARGS_CMAKE}")
    message(FATAL_ERROR "ARGS_CMAKE not found: ${ARGS_CMAKE}")
  endif()
  include("${ARGS_CMAKE}")
  if(DEFINED P101_TIDY_ARGS_LIST)
    set(_args ${P101_TIDY_ARGS_LIST})
  endif()
endif()

if(NOT DEFINED P101_TIDY_FILES_LIST)
  message(FATAL_ERROR "FILES_CMAKE did not define P101_TIDY_FILES_LIST")
endif()

set(_fail 0)
foreach(F IN LISTS P101_TIDY_FILES_LIST)
  if(F STREQUAL "")
    continue()
  endif()
  execute_process(
    COMMAND "${CLANG_TIDY_EXEC}" -p "${DB}" ${_args} "${F}"
    RESULT_VARIABLE _rv
    OUTPUT_VARIABLE _out
    ERROR_VARIABLE  _err
  )
  if(NOT _rv EQUAL 0)
    message(STATUS "clang-tidy failed for: ${F}")
    if(NOT _out STREQUAL "")
      message(STATUS "${_out}")
    endif()
    if(NOT _err STREQUAL "")
      message(STATUS "${_err}")
    endif()
    set(_fail 1)
  endif()
endforeach()

if(_fail)
  message(FATAL_ERROR "clang-tidy reported failures")
endif()
]=])

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

set(P101_ANALYZE_DB_SCRIPT "${CMAKE_BINARY_DIR}/RunAnalyzeFromCompileDB.py")
file(WRITE "${P101_ANALYZE_DB_SCRIPT}" [=[
#!/usr/bin/env python3
import json, os, shlex, subprocess, sys

def is_source_file(p: str) -> bool:
    ext = os.path.splitext(p)[1].lower()
    return ext in [".c", ".cc", ".cpp", ".cxx", ".m", ".mm"]

def strip_output_flags(argv):
    out = []
    it = iter(range(len(argv)))
    skip_next = False
    for i in it:
        if skip_next:
            skip_next = False
            continue
        a = argv[i]
        if a == "-c":
            continue
        if a == "-o":
            skip_next = True
            continue
        if a.startswith("-o") and len(a) > 2:
            continue
        out.append(a)
    return out

def main():
    if len(sys.argv) != 4:
        print("Usage: RunAnalyzeFromCompileDB.py <compile_commands.json> <out_dir> <fail_on_diag 0|1>", file=sys.stderr)
        return 2

    db_path, out_dir, fail_flag = sys.argv[1], sys.argv[2], sys.argv[3]
    fail_on_diag = (fail_flag == "1")
    os.makedirs(out_dir, exist_ok=True)

    with open(db_path, "r", encoding="utf-8") as f:
        db = json.load(f)

    have_diag = False

    for entry in db:
        directory = entry.get("directory", "") or None
        file_ = entry.get("file", "")
        if not file_ or not is_source_file(file_):
            continue

        # Determine argv
        if "arguments" in entry and isinstance(entry["arguments"], list):
            argv = entry["arguments"]
        else:
            cmd = entry.get("command", "")
            argv = shlex.split(cmd)

        if not argv:
            continue

        # Convert to syntax-only while preserving TU flags/defs/includes
        argv = strip_output_flags(argv)
        if not argv:
            continue
        cmd = argv[:1] + ["-fsyntax-only"] + argv[1:]

        # Stable log filename
        relkey = file_.replace("/", "_").replace("\\", "_")
        out_txt = os.path.join(out_dir, f"{relkey}.txt")

        try:
            p = subprocess.run(
                cmd,
                cwd=directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            out = (p.stdout or "").strip()
        except Exception as e:
            out = f"Exception running analyze: {e}"

        with open(out_txt, "w", encoding="utf-8") as fo:
            fo.write(out + ("\n" if out else ""))

        if out:
            have_diag = True

    if have_diag:
        print("Analyzer (syntax-only) produced diagnostics. See:", out_dir)
        if fail_on_diag:
            return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
]=])
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
add_dependencies(${PROJECT_NAME}_analyze_all ${LIBRARY_TARGETS} ${EXECUTABLE_TARGETS})
add_dependencies(${PROJECT_NAME}_tidy_all ${PROJECT_NAME}_analyze_all)

# =========================
# cppcheck — ALWAYS runs (REQUIRED at configure)
# =========================
function(_p101_cppcheck_std OUT_STD OUT_LANG)
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

    # Raspberry Pi / ARM: cppcheck is typically older and doesn't support --check-level
    if (CMAKE_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm|armv7l)$")
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
set(CPPCHECK_FAIL_SCRIPT "${CMAKE_BINARY_DIR}/FailIfCppcheckDiagnostics.cmake")
file(WRITE "${CPPCHECK_FAIL_SCRIPT}" [=[
cmake_minimum_required(VERSION 3.20)
if(NOT DEFINED LOGFILE)
  message(FATAL_ERROR "Need -DLOGFILE=<path>")
endif()
if(NOT EXISTS "${LOGFILE}")
  message(FATAL_ERROR "cppcheck log not found: ${LOGFILE}")
endif()
file(READ "${LOGFILE}" _LOG)
string(REGEX MATCHALL ":[ \\t](warning|style|performance|portability|information|error):" _HITS "${_LOG}")
list(LENGTH _HITS _COUNT)
if(_COUNT GREATER 0)
  message(STATUS "cppcheck reported ${_COUNT} diagnostics. Showing first 200 lines:")
  string(REPLACE "\r\n" "\n" _LOG "${_LOG}")
  string(REPLACE "\r"   "\n" _LOG "${_LOG}")
  string(REPLACE "\n" ";" _LINES "${_LOG}")
  set(_i 0)
  foreach(_line IN LISTS _LINES)
    if(_i GREATER_EQUAL 200)
      message(STATUS "... (truncated)")
      break()
    endif()
    message(STATUS "${_line}")
    math(EXPR _i "${_i}+1")
  endforeach()
  message(FATAL_ERROR "cppcheck found issues. See ${LOGFILE}")
endif()
]=])

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
add_dependencies(${PROJECT_NAME}_cppcheck_all ${LIBRARY_TARGETS} ${EXECUTABLE_TARGETS})
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

    # std flag for clang --analyze
    set(_SA_STD "")
    if (PROJECT_LANGUAGE STREQUAL "CXX")
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
    elseif (UNIX)
        list(APPEND _P101_SA_DEFS -D_GNU_SOURCE)
    endif()

    if (CMAKE_SYSTEM_NAME MATCHES "FreeBSD|DragonFly|OpenBSD|NetBSD")
        list(APPEND _P101_SA_DEFS -D__BSD_VISIBLE=1)
    endif ()

    string(JOIN " " _P101_SA_DEFS_CMD ${_P101_SA_DEFS})

    set(_SA_STAMPS)
    foreach (_abs IN LISTS _ALL_SOURCES_FOR_FORMAT)
        if (NOT _abs MATCHES "^${CMAKE_CURRENT_SOURCE_DIR}/")
            continue()
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

    set(CLANG_SA_FAIL_SCRIPT "${CMAKE_BINARY_DIR}/FailIfClangSADiagnostics.cmake")
    file(WRITE "${CLANG_SA_FAIL_SCRIPT}" [=[
cmake_minimum_required(VERSION 3.20)
if(NOT DEFINED DIR)
  message(FATAL_ERROR "Need -DDIR=<clang-sa logs dir>")
endif()
file(GLOB _FILES "${DIR}/*.txt")
set(_HAVE 0)
foreach(F ${_FILES})
  file(READ "${F}" C)
  string(STRIP "${C}" C)
  if(NOT C STREQUAL "")
    set(_HAVE 1)
    break()
  endif()
endforeach()
if(_HAVE)
  message(STATUS "Clang Static Analyzer produced diagnostics; showing up to first 200 lines:")
  set(_i 0)
  foreach(F ${_FILES})
    file(READ "${F}" C)
    string(REPLACE "\r\n" "\n" C "${C}")
    string(REPLACE "\r"   "\n" C "${C}")
    string(REPLACE "\n" ";" L "${C}")
    foreach(line ${L})
      if(_i GREATER_EQUAL 200)
        message(STATUS "... (truncated)")
        break()
      endif()
      message(STATUS "${line}")
      math(EXPR _i "${_i}+1")
    endforeach()
    if(_i GREATER_EQUAL 200)
      break()
    endif()
  endforeach()
  if("$ENV{P101_CLANG_SA_FAIL}" STREQUAL "1")
    message(FATAL_ERROR "CSA found diagnostics. See ${DIR}/*.txt")
  endif()
endif()
]=])

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
        if (_habspath MATCHES "^${PUBLIC_INC_DIR}/")
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
