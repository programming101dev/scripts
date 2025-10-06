cmake_minimum_required(VERSION 3.21)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# =============================  Project config  =============================
if (NOT EXISTS "${CMAKE_SOURCE_DIR}/config.cmake")
    message(FATAL_ERROR "Missing config.cmake, define project settings there.")
endif ()
include("${CMAKE_SOURCE_DIR}/config.cmake")

set(_REQ_VARS PROJECT_NAME PROJECT_VERSION PROJECT_DESCRIPTION PROJECT_LANGUAGE)
foreach (_v IN LISTS _REQ_VARS)
    if (NOT DEFINED ${_v} OR "${${_v}}" STREQUAL "")
        message(FATAL_ERROR "${_v} is not defined or empty in config.cmake")
    endif ()
endforeach ()

string(TOUPPER "${PROJECT_LANGUAGE}" _PLANG)
if (NOT _PLANG MATCHES "^(C|CXX)$")
    message(FATAL_ERROR "PROJECT_LANGUAGE must be C or CXX, got '${PROJECT_LANGUAGE}'")
endif ()
if ((NOT EXECUTABLE_TARGETS) AND (NOT LIBRARY_TARGETS))
    message(FATAL_ERROR "At least one of EXECUTABLE_TARGETS or LIBRARY_TARGETS must be nonempty.")
endif ()

project(${PROJECT_NAME} VERSION ${PROJECT_VERSION} DESCRIPTION "${PROJECT_DESCRIPTION}" LANGUAGES ${PROJECT_LANGUAGE})

message(STATUS "Loaded config.cmake variables:")
message(STATUS "  PROJECT_NAME: ${PROJECT_NAME}")
message(STATUS "  PROJECT_VERSION: ${PROJECT_VERSION}")
message(STATUS "  PROJECT_DESCRIPTION: ${PROJECT_DESCRIPTION}")
message(STATUS "  PROJECT_LANGUAGE: ${PROJECT_LANGUAGE}")
message(STATUS "  EXECUTABLE_TARGETS: ${EXECUTABLE_TARGETS}")
message(STATUS "  LIBRARY_TARGETS: ${LIBRARY_TARGETS}")
message(STATUS "  STANDARD_FLAGS: ${STANDARD_FLAGS}")
message(STATUS "  SANITIZER_LIST: ${SANITIZER_LIST}")

# =============================  System paths  =============================
set(EXTERNAL_INCLUDE_DIRS "")
foreach (_inc IN ITEMS /usr/local/include)
    if (EXISTS "${_inc}")
        list(APPEND EXTERNAL_INCLUDE_DIRS "${_inc}")
    endif ()
endforeach ()
set(EXTERNAL_LIBRARY_DIRS "")
foreach (_lib IN ITEMS /usr/local/lib /usr/local/lib64)
    if (EXISTS "${_lib}")
        list(APPEND EXTERNAL_LIBRARY_DIRS "${_lib}")
    endif ()
endforeach ()
foreach (_inc ${EXTERNAL_INCLUDE_DIRS})
    include_directories(BEFORE SYSTEM "${_inc}")
endforeach ()
foreach (_lib ${EXTERNAL_LIBRARY_DIRS})
    link_directories("${_lib}")
endforeach ()

set(LOCAL_INCLUDE_DIR "${CMAKE_SOURCE_DIR}/include")
set(HAVE_LOCAL_INCLUDE_DIR FALSE)
if (EXISTS "${LOCAL_INCLUDE_DIR}")
    set(HAVE_LOCAL_INCLUDE_DIR TRUE)
endif ()
set(BUILD_INCLUDE_IF_EXISTS $<$<BOOL:${HAVE_LOCAL_INCLUDE_DIR}>:${LOCAL_INCLUDE_DIR}>)

add_library(sys_paths INTERFACE)
if (EXTERNAL_INCLUDE_DIRS)
    target_include_directories(sys_paths INTERFACE ${EXTERNAL_INCLUDE_DIRS})
endif ()
if (EXTERNAL_LIBRARY_DIRS)
    target_link_directories(sys_paths INTERFACE ${EXTERNAL_LIBRARY_DIRS})
endif ()

set(CMAKE_INSTALL_RPATH_USE_LINK_PATH ON)
include(GNUInstallDirs)
if (NOT CMAKE_INSTALL_INCLUDEDIR)
    set(CMAKE_INSTALL_INCLUDEDIR include)
endif ()
if (NOT CMAKE_INSTALL_LIBDIR)
    set(CMAKE_INSTALL_LIBDIR lib)
endif ()
if (NOT CMAKE_INSTALL_BINDIR)
    set(CMAKE_INSTALL_BINDIR bin)
endif ()
message(STATUS "Install dirs: inc='${CMAKE_INSTALL_INCLUDEDIR}', lib='${CMAKE_INSTALL_LIBDIR}', bin='${CMAKE_INSTALL_BINDIR}'")

# =============================  Global build opts  =============================
set(CMAKE_C_VISIBILITY_PRESET default)
set(CMAKE_CXX_VISIBILITY_PRESET default)
set(CMAKE_VISIBILITY_INLINES_HIDDEN OFF)
option(BUILD_SHARED_LIBS "Build shared libraries" ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

if (_PLANG STREQUAL "C")
    set(_COMPILER_EXE "${CMAKE_C_COMPILER}")
else ()
    set(_COMPILER_EXE "${CMAKE_CXX_COMPILER}")
endif ()
message(STATUS "Using Compiler: ${_COMPILER_EXE}")
get_filename_component(COMPILER_NAME "${_COMPILER_EXE}" NAME)
message(STATUS "Compiler Name: ${COMPILER_NAME}")

# =============================  Tools discovery  =============================
if (NOT DEFINED CLANG_FORMAT_NAME)
    set(CLANG_FORMAT_NAME clang-format)
endif ()
if (NOT DEFINED CLANG_TIDY_NAME)
    set(CLANG_TIDY_NAME clang-tidy)
endif ()
if (NOT DEFINED CPPCHECK_NAME)
    set(CPPCHECK_NAME cppcheck)
endif ()

find_program(CLANG_FORMAT NAMES ${CLANG_FORMAT_NAME})
find_program(CLANG_TIDY NAMES ${CLANG_TIDY_NAME})
find_program(CPPCHECK NAMES ${CPPCHECK_NAME})
find_program(CLANG_ANALYZER_EXE NAMES clang clang-20 clang-19 clang-18)

message(STATUS "Found clang-format: ${CLANG_FORMAT}")
message(STATUS "Found clang-tidy:   ${CLANG_TIDY}")
message(STATUS "Found cppcheck:     ${CPPCHECK}")
message(STATUS "Found clang (for analyzer/tidy resource-dir): ${CLANG_ANALYZER_EXE}")

if (NOT CLANG_FORMAT OR NOT CLANG_TIDY OR NOT CPPCHECK)
    message(FATAL_ERROR "Required tools (clang-format, clang-tidy, cppcheck) not found.")
endif ()

# ---------- Resolve Clang resource dir for headers like <stdatomic.h> ----------
set(CLANG_RESOURCE_DIR "")
if (CLANG_ANALYZER_EXE)
    execute_process(
            COMMAND ${CLANG_ANALYZER_EXE} -print-resource-dir
            OUTPUT_VARIABLE CLANG_RESOURCE_DIR
            OUTPUT_STRIP_TRAILING_WHITESPACE
            ERROR_QUIET)
endif ()
if (CLANG_RESOURCE_DIR)
    message(STATUS "Clang resource dir: ${CLANG_RESOURCE_DIR}")
else ()
    message(WARNING "Could not determine Clang resource dir, clang-tidy may pick GCC headers.")
endif ()

# ---------- Build -extra-arg-before bundle for clang-tidy ----------
function(collect_tidy_clang_sysincs_c OUT)
    set(_clang ${CLANG_ANALYZER_EXE})
    if (NOT _clang AND CMAKE_C_COMPILER_ID MATCHES ".*Clang")
        set(_clang ${CMAKE_C_COMPILER})
    endif()
    if (NOT _clang)
        set(${OUT} "" PARENT_SCOPE)
        return()
    endif()
    execute_process(
            COMMAND ${_clang} -E -x c - -v
            INPUT_FILE /dev/null
            OUTPUT_QUIET
            ERROR_VARIABLE _err)
    string(REGEX REPLACE "\r?\n" ";" _lines "${_err}")
    set(_grab OFF)
    set(_incs "")
    foreach(L IN LISTS _lines)
        if(L MATCHES "^#include <.*> search starts here:")
            set(_grab ON)
            continue()
        endif()
        if(_grab AND L MATCHES "^End of search list\\.")
            break()
        endif()
        if(_grab)
            string(STRIP "${L}" L)
            if(EXISTS "${L}")
                list(APPEND _incs "${L}")
            endif()
        endif()
    endforeach()
    if (CLANG_RESOURCE_DIR)
        list(FILTER _incs EXCLUDE REGEX "^${CLANG_RESOURCE_DIR}/include/?$")
    endif()
    set(${OUT} "${_incs}" PARENT_SCOPE)
endfunction()

set(TIDY_EXTRA_BEFORE_ARGS "")
if (CLANG_RESOURCE_DIR)
    list(APPEND TIDY_EXTRA_BEFORE_ARGS
            -resource-dir "${CLANG_RESOURCE_DIR}"
            -isystem "${CLANG_RESOURCE_DIR}/include")
endif()
if (UNIX AND NOT APPLE)
    collect_tidy_clang_sysincs_c(_CLANG_SYSINCS)
    list(APPEND TIDY_EXTRA_BEFORE_ARGS -nostdinc)
    if (CLANG_RESOURCE_DIR)
        list(APPEND TIDY_EXTRA_BEFORE_ARGS -isystem "${CLANG_RESOURCE_DIR}/include")
    endif()
    foreach(_d IN LISTS _CLANG_SYSINCS)
        list(APPEND TIDY_EXTRA_BEFORE_ARGS -isystem "${_d}")
    endforeach()
endif()
set(TIDY_EXTRA_BEFORE "")
foreach(arg IN LISTS TIDY_EXTRA_BEFORE_ARGS)
    list(APPEND TIDY_EXTRA_BEFORE "-extra-arg-before=${arg}")
endforeach()

# =============================  Probe .flags helpers  =============================
function(get_flags_dir OUTVAR)
    if (_PLANG STREQUAL "C")
        get_filename_component(_base "${CMAKE_C_COMPILER}" NAME)
        set(_id "${CMAKE_C_COMPILER_ID}")
    else ()
        get_filename_component(_base "${CMAKE_CXX_COMPILER}" NAME)
        set(_id "${CMAKE_CXX_COMPILER_ID}")
    endif ()
    set(_try1 "${CMAKE_SOURCE_DIR}/.flags/${_base}")
    set(_try2 "${CMAKE_SOURCE_DIR}/.flags/${_id}")
    if (EXISTS "${_try1}")
        set(${OUTVAR} "${_try1}" PARENT_SCOPE)
    elseif (EXISTS "${_try2}")
        set(${OUTVAR} "${_try2}" PARENT_SCOPE)
    else ()
        message(WARNING "No .flags directory for '${_base}' or '${_id}'. Looked in:\n  ${_try1}\n  ${_try2}")
        set(${OUTVAR} "" PARENT_SCOPE)
    endif ()
endfunction()

function(read_flags_file FILEPATH OUTLIST)
    if (NOT EXISTS "${FILEPATH}")
        set(${OUTLIST} "" PARENT_SCOPE)
        return()
    endif ()
    set(_tokens "")
    file(STRINGS "${FILEPATH}" _lines)
    foreach (_L IN LISTS _lines)
        string(STRIP "${_L}" _L)
        if (_L STREQUAL "" OR _L MATCHES "^[ \t]*#")
            continue()
        endif ()
        string(REGEX REPLACE "^\"(.*)\"$" "\\1" _L "${_L}")
        separate_arguments(_split UNIX_COMMAND "${_L}")
        list(APPEND _tokens ${_split})
    endforeach ()
    list(TRANSFORM _tokens REPLACE "[\r“”‘’]" "")
    set(${OUTLIST} "${_tokens}" PARENT_SCOPE)
endfunction()

function(load_flag_group GROUP_BASENAME OUTLIST)
    get_flags_dir(_FDIR)
    if (_FDIR STREQUAL "")
        set(${OUTLIST} "" PARENT_SCOPE)
        return()
    endif ()
    read_flags_file("${_FDIR}/${GROUP_BASENAME}.txt" _TOK)
    if (_TOK)
        message(STATUS "Loaded ${GROUP_BASENAME} from ${_FDIR}/${GROUP_BASENAME}.txt")
    endif ()
    set(${OUTLIST} "${_TOK}" PARENT_SCOPE)
endfunction()

function(load_sanitizer_flags OUTPUT_VAR)
    if (NOT DEFINED SANITIZER_LIST OR "${SANITIZER_LIST}" STREQUAL "")
        set(${OUTPUT_VAR} "" PARENT_SCOPE)
        return()
    endif ()
    if (APPLE AND (CMAKE_${_PLANG}_COMPILER_ID STREQUAL "GNU"))
        message(WARNING "GCC on macOS cannot link sanitizer runtimes; ignoring SANITIZER_LIST='${SANITIZER_LIST}'. Use clang on macOS.")
        set(${OUTPUT_VAR} "" PARENT_SCOPE)
        return()
    endif ()
    get_flags_dir(_FDIR)
    if (_FDIR STREQUAL "")
        set(${OUTPUT_VAR} "" PARENT_SCOPE)
        return()
    endif ()
    set(SAN_FLAGS "")
    string(REPLACE "," ";" SANITIZER_NAMES "${SANITIZER_LIST}")
    foreach (SAN ${SANITIZER_NAMES})
        read_flags_file("${_FDIR}/${SAN}_sanitizer_flags.txt" _SAN_TOK)
        foreach (TK IN LISTS _SAN_TOK)
            if (TK MATCHES "^-fsanitize=")
                list(APPEND SAN_FLAGS "${TK}")
            endif ()
        endforeach ()
    endforeach ()
    list(REMOVE_DUPLICATES SAN_FLAGS)
    set(${OUTPUT_VAR} "${SAN_FLAGS}" PARENT_SCOPE)
endfunction()

# =============================  Load probed groups  =============================
load_flag_group("analyzer_flags" P101_ANALYZER_FLAGS)
load_flag_group("code_generation_flags" P101_CODEGEN_FLAGS)
load_flag_group("debug_flags" P101_DEBUG_FLAGS)
load_flag_group("instrumentation_flags" P101_INSTR_FLAGS)
load_flag_group("optimization_flags" P101_OPT_FLAGS)
load_flag_group("warning_flags" P101_WARNING_FLAGS)
load_sanitizer_flags(P101_SANITIZER_FLAGS)

message(STATUS "Loaded probed flags (for build):")
message(STATUS "  analyzer:        ${P101_ANALYZER_FLAGS}")
message(STATUS "  code_generation: ${P101_CODEGEN_FLAGS}")
message(STATUS "  debug:           ${P101_DEBUG_FLAGS}")
message(STATUS "  instrumentation:${P101_INSTR_FLAGS}")
message(STATUS "  optimization:    ${P101_OPT_FLAGS}")
message(STATUS "  warning:         ${P101_WARNING_FLAGS}")
if (P101_SANITIZER_FLAGS)
    message(STATUS "  sanitizers:      ${P101_SANITIZER_FLAGS}")
endif ()

# =============================  Interface compile opts  =============================
add_library(project_options INTERFACE)
foreach (_lib ${EXTERNAL_LIBRARY_DIRS})
    target_link_directories(project_options INTERFACE "${_lib}")
endforeach ()
target_compile_options(project_options INTERFACE
        ${STANDARD_FLAGS}
        ${P101_ANALYZER_FLAGS}
        ${P101_CODEGEN_FLAGS}
        ${P101_DEBUG_FLAGS}
        ${P101_INSTR_FLAGS}
        ${P101_OPT_FLAGS}
        ${P101_WARNING_FLAGS}
)
if (P101_SANITIZER_FLAGS)
    target_compile_options(project_options INTERFACE ${P101_SANITIZER_FLAGS} -fno-omit-frame-pointer)
    target_link_options(project_options INTERFACE ${P101_SANITIZER_FLAGS})
endif ()
if (P101_INSTR_FLAGS)
    target_link_options(project_options INTERFACE ${P101_INSTR_FLAGS})
endif ()

# =============================  Utility macros  =============================
macro(MAKE_ABSOLUTE_PATHS VAR_NAME)
    set(_TMP "")
    foreach (_f ${${VAR_NAME}})
        if (IS_ABSOLUTE "${_f}")
            list(APPEND _TMP "${_f}")
        else ()
            list(APPEND _TMP "${CMAKE_SOURCE_DIR}/${_f}")
        endif ()
    endforeach ()
    set(${VAR_NAME} ${_TMP})
endmacro()

# =============================  clang-format helper script  =============================
set(RUN_CF "${CMAKE_BINARY_DIR}/run-clang-format.cmake")
file(WRITE "${RUN_CF}" [=[
if(NOT DEFINED CLANG_FORMAT)
  message(FATAL_ERROR "CLANG_FORMAT not set")
endif()

if(NOT DEFINED FILES)
  message(FATAL_ERROR "FILES not set")
endif()

string(REPLACE "|" ";" file_list "${FILES}")
list(LENGTH file_list nfiles)
message(STATUS "clang-format: formatting ${nfiles} files")

foreach(f IN LISTS file_list)
  if(EXISTS "${f}")
    execute_process(COMMAND "${CLANG_FORMAT}" --style=file -i "${f}")
  else()
    message(WARNING "clang-format: missing file '${f}'")
  endif()
endforeach()
]=])

# Aggregator placeholder
if (NOT TARGET format)
    add_custom_target(format)
endif()

# =============================  Collect-only attach for target files =============================
# Records files and target names; real formatting is created after all targets are declared.
function(attach_clang_format TARGET_NAME)
    if (NOT TARGET ${TARGET_NAME})
        message(FATAL_ERROR "attach_clang_format called for non-existent target '${TARGET_NAME}'")
    endif()
    set(_FILES ${ARGN})
    if (_FILES)
        # remember files
        set_property(GLOBAL APPEND PROPERTY P101_FORMAT_FILES "${_FILES}")
    endif()
    # remember the target
    set_property(GLOBAL APPEND PROPERTY P101_FORMAT_TARGETS "${TARGET_NAME}")
endfunction()

# =============================  Targets: exe/lib  =============================
set(ALL_SOURCE_FILES "")
set(ALL_HEADER_FILES "")

if (EXECUTABLE_TARGETS)
    foreach (EXE ${EXECUTABLE_TARGETS})
        MAKE_ABSOLUTE_PATHS(${EXE}_SOURCES)
        MAKE_ABSOLUTE_PATHS(${EXE}_HEADERS)
        list(APPEND ALL_SOURCE_FILES ${${EXE}_SOURCES})
        list(APPEND ALL_HEADER_FILES ${${EXE}_HEADERS})
        message(STATUS "Executable ${EXE}: Sources: ${${EXE}_SOURCES}, Headers: ${${EXE}_HEADERS}")

        add_executable(${EXE})
        target_sources(${EXE} PRIVATE ${${EXE}_SOURCES})
        target_include_directories(${EXE} PUBLIC ${BUILD_INCLUDE_IF_EXISTS} $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)

        set(_EXE_FINAL_LIBS "")
        foreach (LINK_LIB ${${EXE}_LINK_LIBRARIES})
            if (TARGET ${LINK_LIB})
                list(APPEND _EXE_FINAL_LIBS ${LINK_LIB})
            else ()
                set(_FIND_VAR "${EXE}_${LINK_LIB}_FOUND")
                find_library(${_FIND_VAR} NAMES ${LINK_LIB} PATHS /usr/local/lib /usr/local/lib64)
                if (NOT "${${_FIND_VAR}}" MATCHES "-NOTFOUND$")
                    message(STATUS "✅ Found system library for ${EXE}: ${LINK_LIB} -> ${${_FIND_VAR}}")
                elseif ("${LINK_LIB}" STREQUAL "iconv" AND APPLE AND (CMAKE_${_PLANG}_COMPILER_ID MATCHES ".*Clang"))
                    message(STATUS "🟡 Manually linking -liconv for ${EXE} on macOS with Clang/AppleClang")
                else ()
                    message(WARNING "Library '${LINK_LIB}' for '${EXE}' not found; relying on default linker search paths.")
                endif ()
                list(APPEND _EXE_FINAL_LIBS ${LINK_LIB})
            endif ()
        endforeach ()
        target_link_libraries(${EXE} PRIVATE project_options sys_paths ${_EXE_FINAL_LIBS})

        # record only target's files for the global formatting step
        attach_clang_format(${EXE} ${${EXE}_SOURCES} ${${EXE}_HEADERS})
    endforeach ()
endif ()

if (LIBRARY_TARGETS)
    foreach (LIB ${LIBRARY_TARGETS})
        MAKE_ABSOLUTE_PATHS(${LIB}_SOURCES)
        MAKE_ABSOLUTE_PATHS(${LIB}_HEADERS)
        list(APPEND ALL_SOURCE_FILES ${${LIB}_SOURCES})
        list(APPEND ALL_HEADER_FILES ${${LIB}_HEADERS})
        message(STATUS "Library ${LIB}: Sources: ${${LIB}_SOURCES}, Headers: ${${LIB}_HEADERS}")

        set(FINAL_LINK_LIBRARIES "")
        foreach (LINK_LIB ${${LIB}_LINK_LIBRARIES})
            if (TARGET ${LINK_LIB})
                message(STATUS "✅ Found project target: ${LINK_LIB}")
                list(APPEND FINAL_LINK_LIBRARIES ${LINK_LIB})
            else ()
                set(_FIND_VAR "${LIB}_${LINK_LIB}_FOUND")
                find_library(${_FIND_VAR} NAMES ${LINK_LIB} PATHS /usr/local/lib /usr/local/lib64)
                if (NOT "${${_FIND_VAR}}" MATCHES "-NOTFOUND$")
                    message(STATUS "✅ Found system library: ${LINK_LIB} -> ${${_FIND_VAR}}")
                    list(APPEND FINAL_LINK_LIBRARIES ${LINK_LIB})
                elseif ("${LINK_LIB}" STREQUAL "iconv" AND APPLE AND (CMAKE_${_PLANG}_COMPILER_ID MATCHES ".*Clang"))
                    message(STATUS "🟡 Manually linking -liconv on macOS with Clang/AppleClang")
                    list(APPEND FINAL_LINK_LIBRARIES iconv)
                else ()
                    message(WARNING "❌ Optional link library '${LINK_LIB}' for '${LIB}' not found. Skipping.")
                endif ()
            endif ()
        endforeach ()

        add_library(${LIB})
        target_sources(${LIB} PRIVATE ${${LIB}_SOURCES})
        target_include_directories(${LIB} PUBLIC ${BUILD_INCLUDE_IF_EXISTS} $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)
        target_link_libraries(${LIB} PRIVATE project_options sys_paths ${FINAL_LINK_LIBRARIES})

        # record only target's files for the global formatting step
        attach_clang_format(${LIB} ${${LIB}_SOURCES} ${${LIB}_HEADERS})
    endforeach ()
endif ()

# =============================  Real formatting step built from recorded files =============================
# Union of all files provided by targets
get_property(_FMT_FILES GLOBAL PROPERTY P101_FORMAT_FILES)
if (_FMT_FILES)
    list(REMOVE_DUPLICATES _FMT_FILES)
    list(JOIN _FMT_FILES "|" _FMT_FILES_JOINED)
    set(_STAMP "${CMAKE_BINARY_DIR}/format.targets.stamp")
    add_custom_command(
            OUTPUT "${_STAMP}"
            COMMAND ${CMAKE_COMMAND} -DCLANG_FORMAT=${CLANG_FORMAT} -DFILES=${_FMT_FILES_JOINED} -P "${RUN_CF}"
            COMMAND ${CMAKE_COMMAND} -E touch "${_STAMP}"
            DEPENDS ${_FMT_FILES}
            VERBATIM
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
            COMMENT "clang-format all files that belong to declared targets")
    add_custom_target(format_targets DEPENDS "${_STAMP}")
else()
    add_custom_target(format_targets) # present but empty
endif()
add_dependencies(format format_targets)

# Ensure each build target waits for formatting
get_property(_FMT_TGTS GLOBAL PROPERTY P101_FORMAT_TARGETS)
if (_FMT_TGTS)
    list(REMOVE_DUPLICATES _FMT_TGTS)
    foreach(_t IN LISTS _FMT_TGTS)
        if (TARGET ${_t})
            add_dependencies(${_t} format_targets)
        endif()
    endforeach()
endif()

# =============================  Tool wiring  =============================
# Extract only -D* from STANDARD_FLAGS for tools
set(TOOL_DEFINES "")
foreach (tok IN LISTS STANDARD_FLAGS)
    if (tok MATCHES "^-D")
        list(APPEND TOOL_DEFINES "${tok}")
    endif ()
endforeach ()

# Common -I for tools
set(TOOL_I_FLAGS "")
if (HAVE_LOCAL_INCLUDE_DIR)
    list(APPEND TOOL_I_FLAGS -I${LOCAL_INCLUDE_DIR})
endif ()
foreach (_inc ${EXTERNAL_INCLUDE_DIRS})
    list(APPEND TOOL_I_FLAGS -I${_inc})
endforeach ()

# ---------- clang-tidy (DB-less, C-only) ----------
if(PROJECT_LANGUAGE STREQUAL "C")

    set(_TIDY_LANG c)
    set(_TIDY_STD -std=c11)

    set(TIDY_HEADER_FILTER "^${CMAKE_SOURCE_DIR}")

    set(_TIDY_I_FLAGS "")
    if(HAVE_LOCAL_INCLUDE_DIR)
        list(APPEND _TIDY_I_FLAGS -I${LOCAL_INCLUDE_DIR})
    endif()
    foreach(_inc ${EXTERNAL_INCLUDE_DIRS})
        list(APPEND _TIDY_I_FLAGS -isystem ${_inc})
    endforeach()

    set(TIDY_CHECKS_LIST
            "*"
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
            "-llvm-header-guard")
    list(JOIN TIDY_CHECKS_LIST "," TIDY_CHECKS_ARG)

    set(_TIDY_WARN_AS_ERRORS "\\*")
    set(TIDY_TU_INPUTS ${ALL_SOURCE_FILES})

    if(TIDY_TU_INPUTS)
        add_custom_target(${PROJECT_NAME}__clang-tidy
                COMMAND ${CLANG_TIDY} ${TIDY_EXTRA_BEFORE} ${TIDY_TU_INPUTS}
                -quiet
                --header-filter=${TIDY_HEADER_FILTER}
                --system-headers=false
                --warnings-as-errors=${_TIDY_WARN_AS_ERRORS}
                -checks=${TIDY_CHECKS_ARG}
                -- -x ${_TIDY_LANG} ${_TIDY_STD}
                -Wno-unknown-warning-option
                -Wno-unused-command-line-argument
                ${TOOL_DEFINES}
                ${_TIDY_I_FLAGS}
                COMMENT "Running clang-tidy (C only, DB-less, Clang headers forced)"
                WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
                COMMAND_EXPAND_LISTS)
        set_property(TARGET ${PROJECT_NAME}__clang-tidy PROPERTY VERBATIM ON)
        add_dependencies(${PROJECT_NAME}__clang-tidy format)  # analyze formatted code
    endif()

    if(ALL_HEADER_FILES)
        add_custom_target(${PROJECT_NAME}__clang-tidy_headers
                COMMAND ${CLANG_TIDY} ${TIDY_EXTRA_BEFORE} ${ALL_HEADER_FILES}
                -quiet
                --header-filter=${TIDY_HEADER_FILTER}
                --system-headers=false
                --warnings-as-errors=${_TIDY_WARN_AS_ERRORS}
                -checks=${TIDY_CHECKS_ARG}
                -- -x ${_TIDY_LANG} ${_TIDY_STD}
                -Wno-unknown-warning-option
                -Wno-unused-command-line-argument
                ${TOOL_DEFINES}
                ${_TIDY_I_FLAGS}
                COMMENT "Running clang-tidy on headers (C only, DB-less, Clang headers forced)"
                WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
                COMMAND_EXPAND_LISTS)
        set_property(TARGET ${PROJECT_NAME}__clang-tidy_headers PROPERTY VERBATIM ON)
        add_dependencies(${PROJECT_NAME}__clang-tidy_headers format)
        add_dependencies(${PROJECT_NAME}__clang-tidy ${PROJECT_NAME}__clang-tidy_headers)
    endif()

endif()  # PROJECT_LANGUAGE STREQUAL "C"

# ---------- Clang static analyzer (opt-in, default ON for Clang toolchain) ----------
option(ENABLE_CLANG_ANALYZER "Run clang static analyzer" OFF)
if (_PLANG STREQUAL "C")
    set(TOOL_LANG_ARG -x c)
else()
    set(TOOL_LANG_ARG -x c++)
endif ()
if (CMAKE_${_PLANG}_COMPILER_ID MATCHES ".*Clang")
    set(ENABLE_CLANG_ANALYZER_DEFAULT ON)
else ()
    set(ENABLE_CLANG_ANALYZER_DEFAULT OFF)
endif ()
if (ENABLE_CLANG_ANALYZER_DEFAULT OR ENABLE_CLANG_ANALYZER)
    if (CLANG_ANALYZER_EXE AND (ALL_SOURCE_FILES OR ALL_HEADER_FILES))
        add_custom_target(clang-analyze
                COMMAND ${CLANG_ANALYZER_EXE} --analyze --analyzer-output text
                ${TOOL_LANG_ARG}
                -Xclang -analyzer-checker=core
                -Xclang -analyzer-checker=security
                -Xclang -analyzer-disable-checker=security.insecureAPI.DeprecatedOrUnsafeBufferHandling
                ${TOOL_DEFINES} ${TOOL_I_FLAGS} -- ${ALL_SOURCE_FILES}
                COMMENT "Running Clang Static Analyzer (minimal defines/includes)"
                WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
                COMMAND_EXPAND_LISTS)
        add_dependencies(clang-analyze format)
    endif ()
endif ()

# ---------- cppcheck ----------
if (_PLANG STREQUAL "C")
    set(CPPCHECK_STD --std=c11)
else()
    set(CPPCHECK_STD --std=c++20)
endif ()
if (ALL_SOURCE_FILES OR ALL_HEADER_FILES)
    add_custom_target(cppcheck-check
            COMMAND ${CPPCHECK}
            --error-exitcode=1 --force --quiet --inline-suppr --library=posix
            --check-level=exhaustive --enable=all
            --suppress=missingIncludeSystem --suppress=unusedFunction --suppress=staticFunction
            --suppress=constParameterPointer --suppress=unmatchedSuppression --suppress=checkersReport
            ${CPPCHECK_STD} ${TOOL_DEFINES} ${TOOL_I_FLAGS} ${ALL_SOURCE_FILES}
            COMMENT "Running cppcheck (minimal defines/includes)"
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
            COMMAND_EXPAND_LISTS)
    add_dependencies(cppcheck-check format)
endif ()

# ---------- aggregate 'check' ----------
add_custom_target(check ALL)
if (TARGET ${PROJECT_NAME}__clang-tidy)
    add_dependencies(check ${PROJECT_NAME}__clang-tidy)
endif ()
if (TARGET cppcheck-check)
    add_dependencies(check cppcheck-check)
endif ()
if (TARGET clang-analyze)
    add_dependencies(check clang-analyze)
endif ()
add_dependencies(check format)

# =============================  Install rules  =============================
if (LIBRARY_TARGETS)
    install(TARGETS ${LIBRARY_TARGETS}
            EXPORT ${PROJECT_NAME}Targets
            LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
            ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
            RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
endif ()
if (EXECUTABLE_TARGETS)
    install(TARGETS ${EXECUTABLE_TARGETS}
            EXPORT ${PROJECT_NAME}Targets
            RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
endif ()
if (HAVE_LOCAL_INCLUDE_DIR)
    install(DIRECTORY ${LOCAL_INCLUDE_DIR}/
            DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
            FILES_MATCHING PATTERN "*.h")
endif ()
if (EXECUTABLE_TARGETS OR LIBRARY_TARGETS)
    install(EXPORT ${PROJECT_NAME}Targets
            FILE ${PROJECT_NAME}Targets.cmake
            NAMESPACE ${PROJECT_NAME}::
            DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/${PROJECT_NAME})
endif ()
