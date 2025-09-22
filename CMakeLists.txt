cmake_minimum_required(VERSION 3.21)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# =============================
# Load project configuration
# =============================
if(NOT EXISTS "${CMAKE_SOURCE_DIR}/config.cmake")
    message(FATAL_ERROR "Missing config.cmake file. This file is required to define project settings.")
endif()
include("${CMAKE_SOURCE_DIR}/config.cmake")

# Echo inputs
message(STATUS "Loaded config.cmake variables:")
message(STATUS "  PROJECT_NAME: ${PROJECT_NAME}")
message(STATUS "  PROJECT_VERSION: ${PROJECT_VERSION}")
message(STATUS "  PROJECT_DESCRIPTION: ${PROJECT_DESCRIPTION}")
message(STATUS "  PROJECT_LANGUAGE: ${PROJECT_LANGUAGE}")
message(STATUS "  EXECUTABLE_TARGETS: ${EXECUTABLE_TARGETS}")
message(STATUS "  LIBRARY_TARGETS: ${LIBRARY_TARGETS}")
message(STATUS "  STANDARD_FLAGS: ${STANDARD_FLAGS}")
message(STATUS "  SANITIZER_LIST: ${SANITIZER_LIST}")

# Validate
set(REQUIRED_VARS PROJECT_NAME PROJECT_VERSION PROJECT_DESCRIPTION PROJECT_LANGUAGE)
foreach(VAR ${REQUIRED_VARS})
    if(NOT DEFINED ${VAR} OR "${${VAR}}" STREQUAL "")
        message(FATAL_ERROR "${VAR} is not defined or empty in config.cmake")
    endif()
endforeach()

string(TOUPPER "${PROJECT_LANGUAGE}" _PLANG)
if(NOT _PLANG MATCHES "^(C|CXX)$")
    message(FATAL_ERROR "PROJECT_LANGUAGE must be C or CXX, got '${PROJECT_LANGUAGE}'")
endif()

if((NOT EXECUTABLE_TARGETS) AND (NOT LIBRARY_TARGETS))
    message(FATAL_ERROR "At least one of EXECUTABLE_TARGETS or LIBRARY_TARGETS must be nonempty.")
endif()

project(${PROJECT_NAME} VERSION ${PROJECT_VERSION} DESCRIPTION "${PROJECT_DESCRIPTION}" LANGUAGES ${PROJECT_LANGUAGE})

# =============================
# External include/lib prefixes
# =============================
set(EXTERNAL_INCLUDE_DIRS "")
foreach(_inc IN ITEMS /usr/local/include)
    if(EXISTS "${_inc}")
        list(APPEND EXTERNAL_INCLUDE_DIRS "${_inc}")
    endif()
endforeach()

set(EXTERNAL_LIBRARY_DIRS "")
foreach(_lib IN ITEMS /usr/local/lib /usr/local/lib64)
    if(EXISTS "${_lib}")
        list(APPEND EXTERNAL_LIBRARY_DIRS "${_lib}")
    endif()
endforeach()

foreach(_inc ${EXTERNAL_INCLUDE_DIRS})
    include_directories(BEFORE SYSTEM "${_inc}")
    add_compile_options(-isystem "${_inc}")
endforeach()
foreach(_lib ${EXTERNAL_LIBRARY_DIRS})
    link_directories("${_lib}")
endforeach()

# ---- Optional local include dir ----
set(LOCAL_INCLUDE_DIR "${CMAKE_SOURCE_DIR}/include")
if(EXISTS "${LOCAL_INCLUDE_DIR}")
    set(HAVE_LOCAL_INCLUDE_DIR TRUE)
else()
    set(HAVE_LOCAL_INCLUDE_DIR FALSE)
endif()
# Generator-expression that expands to local include only if it exists
set(BUILD_INCLUDE_IF_EXISTS $<$<BOOL:${HAVE_LOCAL_INCLUDE_DIR}>:${LOCAL_INCLUDE_DIR}>)

add_library(sys_paths INTERFACE)
if(EXTERNAL_INCLUDE_DIRS)
    target_include_directories(sys_paths INTERFACE ${EXTERNAL_INCLUDE_DIRS})
endif()
if(EXTERNAL_LIBRARY_DIRS)
    target_link_directories(sys_paths INTERFACE ${EXTERNAL_LIBRARY_DIRS})
endif()

set(CMAKE_INSTALL_RPATH_USE_LINK_PATH ON)
include(GNUInstallDirs)
if(NOT CMAKE_INSTALL_INCLUDEDIR)
    set(CMAKE_INSTALL_INCLUDEDIR include)
endif()
if(NOT CMAKE_INSTALL_LIBDIR)
    set(CMAKE_INSTALL_LIBDIR     lib)
endif()
if(NOT CMAKE_INSTALL_BINDIR)
    set(CMAKE_INSTALL_BINDIR     bin)
endif()
message(STATUS "Install dirs: inc='${CMAKE_INSTALL_INCLUDEDIR}', lib='${CMAKE_INSTALL_LIBDIR}', bin='${CMAKE_INSTALL_BINDIR}'")

# =============================
# Global build options
# =============================
set(CMAKE_C_VISIBILITY_PRESET default)
set(CMAKE_CXX_VISIBILITY_PRESET default)
set(CMAKE_VISIBILITY_INLINES_HIDDEN OFF)
option(BUILD_SHARED_LIBS "Build shared libraries" ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# =============================
# Tools (overridable names)
# =============================
if(NOT DEFINED CLANG_FORMAT_NAME)
    set(CLANG_FORMAT_NAME clang-format)
endif()
if(NOT DEFINED CLANG_TIDY_NAME)
    set(CLANG_TIDY_NAME   clang-tidy)
endif()
if(NOT DEFINED CPPCHECK_NAME)
    set(CPPCHECK_NAME     cppcheck)
endif()

find_program(CLANG_FORMAT NAMES ${CLANG_FORMAT_NAME})
find_program(CLANG_TIDY   NAMES ${CLANG_TIDY_NAME})
find_program(CPPCHECK     NAMES ${CPPCHECK_NAME})
# Optional separate clang for analyzer (used only if enabled)
find_program(CLANG_ANALYZER_EXE NAMES clang clang-20 clang-19 clang-18)

message(STATUS "Found clang-format: ${CLANG_FORMAT}")
message(STATUS "Found clang-tidy:   ${CLANG_TIDY}")
message(STATUS "Found cppcheck:     ${CPPCHECK}")
message(STATUS "Found clang (for analyzer): ${CLANG_ANALYZER_EXE}")

if(NOT CLANG_FORMAT OR NOT CLANG_TIDY OR NOT CPPCHECK)
    message(FATAL_ERROR "Required tools (clang-format, clang-tidy, cppcheck) not found.")
endif()

# =============================
# Print compiler info
# =============================
if(_PLANG STREQUAL "C")
    set(_COMPILER_EXE "${CMAKE_C_COMPILER}")
else()
    set(_COMPILER_EXE "${CMAKE_CXX_COMPILER}")
endif()
message(STATUS "Using Compiler: ${_COMPILER_EXE}")
get_filename_component(COMPILER_NAME "${_COMPILER_EXE}" NAME)
message(STATUS "Compiler Name: ${COMPILER_NAME}")

# =============================
# Helpers to read .flags files (BUILD ONLY)
# =============================
function(get_flags_dir OUTVAR)
    if(_PLANG STREQUAL "C")
        get_filename_component(_base "${CMAKE_C_COMPILER}" NAME)
        set(_id "${CMAKE_C_COMPILER_ID}")
    else()
        get_filename_component(_base "${CMAKE_CXX_COMPILER}" NAME)
        set(_id "${CMAKE_CXX_COMPILER_ID}")
    endif()
    set(_try1 "${CMAKE_SOURCE_DIR}/.flags/${_base}")
    set(_try2 "${CMAKE_SOURCE_DIR}/.flags/${_id}")
    if(EXISTS "${_try1}")
        set(${OUTVAR} "${_try1}" PARENT_SCOPE)
    elseif(EXISTS "${_try2}")
        set(${OUTVAR} "${_try2}" PARENT_SCOPE)
    else()
        message(WARNING "No .flags directory for '${_base}' or '${_id}'. Looked in:\n  ${_try1}\n  ${_try2}")
        set(${OUTVAR} "" PARENT_SCOPE)
    endif()
endfunction()

function(read_flags_file FILEPATH OUTLIST)
    if(NOT EXISTS "${FILEPATH}")
        set(${OUTLIST} "" PARENT_SCOPE)
        return()
    endif()
    set(_tokens "")
    file(STRINGS "${FILEPATH}" _lines)
    foreach(_L IN LISTS _lines)
        string(STRIP "${_L}" _L)
        if(_L STREQUAL "" OR _L MATCHES "^[ \t]*#")
            continue()
        endif()
        string(REGEX REPLACE "^\"(.*)\"$" "\\1" _L "${_L}")
        separate_arguments(_split UNIX_COMMAND "${_L}")
        list(APPEND _tokens ${_split})
    endforeach()
    list(TRANSFORM _tokens REPLACE "[\r“”‘’]" "")
    set(${OUTLIST} "${_tokens}" PARENT_SCOPE)
endfunction()

function(load_flag_group GROUP_BASENAME OUTLIST)
    get_flags_dir(_FDIR)
    if(_FDIR STREQUAL "")
        set(${OUTLIST} "" PARENT_SCOPE)
        return()
    endif()
    read_flags_file("${_FDIR}/${GROUP_BASENAME}.txt" _TOK)
    if(_TOK)
        message(STATUS "Loaded ${GROUP_BASENAME} from ${_FDIR}/${GROUP_BASENAME}.txt")
    endif()
    set(${OUTLIST} "${_TOK}" PARENT_SCOPE)
endfunction()

function(load_sanitizer_flags OUTPUT_VAR)
    if(NOT DEFINED SANITIZER_LIST OR "${SANITIZER_LIST}" STREQUAL "")
        set(${OUTPUT_VAR} "" PARENT_SCOPE)
        return()
    endif()
    if(APPLE AND (CMAKE_${_PLANG}_COMPILER_ID STREQUAL "GNU"))
        message(WARNING "GCC on macOS cannot link sanitizer runtimes; ignoring SANITIZER_LIST='${SANITIZER_LIST}'. Use clang on macOS.")
        set(${OUTPUT_VAR} "" PARENT_SCOPE)
        return()
    endif()
    get_flags_dir(_FDIR)
    if(_FDIR STREQUAL "")
        set(${OUTPUT_VAR} "" PARENT_SCOPE)
        return()
    endif()
    set(SAN_FLAGS "")
    string(REPLACE "," ";" SANITIZER_NAMES "${SANITIZER_LIST}")
    foreach(SAN ${SANITIZER_NAMES})
        read_flags_file("${_FDIR}/${SAN}_sanitizer_flags.txt" _SAN_TOK)
        foreach(TK IN LISTS _SAN_TOK)
            if(TK MATCHES "^-fsanitize=")
                list(APPEND SAN_FLAGS "${TK}")
            endif()
        endforeach()
    endforeach()
    list(REMOVE_DUPLICATES SAN_FLAGS)
    set(${OUTPUT_VAR} "${SAN_FLAGS}" PARENT_SCOPE)
endfunction()

# =============================
# Load probed flag groups (BUILD ONLY)
# =============================
load_flag_group("analyzer_flags"         P101_ANALYZER_FLAGS)
load_flag_group("code_generation_flags"  P101_CODEGEN_FLAGS)
load_flag_group("debug_flags"            P101_DEBUG_FLAGS)
load_flag_group("instrumentation_flags"  P101_INSTR_FLAGS)
load_flag_group("optimization_flags"     P101_OPT_FLAGS)
load_flag_group("warning_flags"          P101_WARNING_FLAGS)
load_sanitizer_flags(P101_SANITIZER_FLAGS)

message(STATUS "Loaded probed flags (for build):")
message(STATUS "  analyzer:        ${P101_ANALYZER_FLAGS}")
message(STATUS "  code_generation: ${P101_CODEGEN_FLAGS}")
message(STATUS "  debug:           ${P101_DEBUG_FLAGS}")
message(STATUS "  instrumentation:${P101_INSTR_FLAGS}")
message(STATUS "  optimization:    ${P101_OPT_FLAGS}")
message(STATUS "  warning:         ${P101_WARNING_FLAGS}")
if(P101_SANITIZER_FLAGS)
    message(STATUS "  sanitizers:      ${P101_SANITIZER_FLAGS}")
endif()

# =============================
# Interface options for builds
# =============================
add_library(project_options INTERFACE)

foreach(_lib ${EXTERNAL_LIBRARY_DIRS})
    target_link_directories(project_options INTERFACE "${_lib}")
endforeach()

target_compile_options(project_options INTERFACE
        ${STANDARD_FLAGS}
        ${P101_ANALYZER_FLAGS}
        ${P101_CODEGEN_FLAGS}
        ${P101_DEBUG_FLAGS}
        ${P101_INSTR_FLAGS}
        ${P101_OPT_FLAGS}
        ${P101_WARNING_FLAGS}
)
if(P101_SANITIZER_FLAGS)
    target_compile_options(project_options INTERFACE ${P101_SANITIZER_FLAGS} -fno-omit-frame-pointer)
    target_link_options(project_options    INTERFACE ${P101_SANITIZER_FLAGS})
endif()
if(P101_INSTR_FLAGS)
    target_link_options(project_options INTERFACE ${P101_INSTR_FLAGS})
endif()

# =============================
# Make paths absolute macro
# =============================
macro(MAKE_ABSOLUTE_PATHS VAR_NAME)
    set(TEMP_LIST "")
    foreach(FILE ${${VAR_NAME}})
        if(IS_ABSOLUTE ${FILE})
            list(APPEND TEMP_LIST ${FILE})
        else()
            list(APPEND TEMP_LIST "${CMAKE_SOURCE_DIR}/${FILE}")
        endif()
    endforeach()
    set(${VAR_NAME} ${TEMP_LIST})
endmacro()

# =============================
# Executables
# =============================
set(ALL_SOURCE_FILES "")
set(ALL_HEADER_FILES "")

if(EXECUTABLE_TARGETS)
    foreach(EXE ${EXECUTABLE_TARGETS})
        MAKE_ABSOLUTE_PATHS(${EXE}_SOURCES)
        MAKE_ABSOLUTE_PATHS(${EXE}_HEADERS)

        list(APPEND ALL_SOURCE_FILES ${${EXE}_SOURCES})
        list(APPEND ALL_HEADER_FILES ${${EXE}_HEADERS})

        message(STATUS "Executable ${EXE}: Sources: ${${EXE}_SOURCES}, Headers: ${${EXE}_HEADERS}")

        add_executable(${EXE})
        target_sources(${EXE} PRIVATE ${${EXE}_SOURCES})
        target_include_directories(${EXE} PUBLIC
                ${BUILD_INCLUDE_IF_EXISTS}
                $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
        )

        set(_EXE_FINAL_LIBS "")
        foreach(LINK_LIB ${${EXE}_LINK_LIBRARIES})
            if(TARGET ${LINK_LIB})
                list(APPEND _EXE_FINAL_LIBS ${LINK_LIB})
            else()
                set(_FIND_VAR "${EXE}_${LINK_LIB}_FOUND")
                find_library(${_FIND_VAR} NAMES ${LINK_LIB} PATHS /usr/local/lib /usr/local/lib64)
                if(NOT "${${_FIND_VAR}}" MATCHES "-NOTFOUND$")
                    message(STATUS "✅ Found system library for ${EXE}: ${LINK_LIB} -> ${${_FIND_VAR}}")
                elseif("${LINK_LIB}" STREQUAL "iconv" AND APPLE AND (CMAKE_${_PLANG}_COMPILER_ID MATCHES ".*Clang"))
                    message(STATUS "🟡 Manually linking -liconv for ${EXE} on macOS with Clang/AppleClang")
                else()
                    message(WARNING "Library '${LINK_LIB}' for '${EXE}' not found; relying on default linker search paths.")
                endif()
                list(APPEND _EXE_FINAL_LIBS ${LINK_LIB})
            endif()
        endforeach()

        target_link_libraries(${EXE} PRIVATE project_options sys_paths ${_EXE_FINAL_LIBS})
        if(TARGET format)
            add_dependencies(${EXE} format)
        endif()
    endforeach()
endif()

# =============================
# Libraries
# =============================
if(LIBRARY_TARGETS)
    foreach(LIB ${LIBRARY_TARGETS})
        MAKE_ABSOLUTE_PATHS(${LIB}_SOURCES)
        MAKE_ABSOLUTE_PATHS(${LIB}_HEADERS)

        list(APPEND ALL_SOURCE_FILES ${${LIB}_SOURCES})
        list(APPEND ALL_HEADER_FILES ${${LIB}_HEADERS})

        message(STATUS "Library ${LIB}: Sources: ${${LIB}_SOURCES}, Headers: ${${LIB}_HEADERS}")

        set(FINAL_LINK_LIBRARIES "")
        foreach(LINK_LIB ${${LIB}_LINK_LIBRARIES})
            if(TARGET ${LINK_LIB})
                message(STATUS "✅ Found project target: ${LINK_LIB}")
                list(APPEND FINAL_LINK_LIBRARIES ${LINK_LIB})
            else()
                set(_FIND_VAR "${LIB}_${LINK_LIB}_FOUND")
                find_library(${_FIND_VAR} NAMES ${LINK_LIB} PATHS /usr/local/lib /usr/local/lib64)
                if(NOT "${${_FIND_VAR}}" MATCHES "-NOTFOUND$")
                    message(STATUS "✅ Found system library: ${LINK_LIB} -> ${${_FIND_VAR}}")
                    list(APPEND FINAL_LINK_LIBRARIES ${LINK_LIB})
                elseif("${LINK_LIB}" STREQUAL "iconv" AND APPLE AND (CMAKE_${_PLANG}_COMPILER_ID MATCHES ".*Clang"))
                    message(STATUS "🟡 Manually linking -liconv on macOS with Clang/AppleClang")
                    list(APPEND FINAL_LINK_LIBRARIES iconv)
                else()
                    message(WARNING "❌ Optional link library '${LINK_LIB}' for '${LIB}' not found. Skipping.")
                endif()
            endif()
        endforeach()

        add_library(${LIB})
        target_sources(${LIB} PRIVATE ${${LIB}_SOURCES})
        target_include_directories(${LIB} PUBLIC
                ${BUILD_INCLUDE_IF_EXISTS}
                $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
        )
        target_link_libraries(${LIB} PRIVATE project_options sys_paths ${FINAL_LINK_LIBRARIES})
        if(TARGET format)
            add_dependencies(${LIB} format)
        endif()
    endforeach()
endif()

# =============================
# Tool targets (EXTRA LINT ONLY)
# Minimal flags: just feature-test macros + include paths (optional local).
# =============================

# Extract only -D* tokens from STANDARD_FLAGS (to feed tools)
set(TOOL_DEFINES "")
foreach(tok IN LISTS STANDARD_FLAGS)
    if(tok MATCHES "^-D")
        list(APPEND TOOL_DEFINES "${tok}")
    endif()
endforeach()
# Extract only -D* from STANDARD_FLAGS for tools; do NOT add anything implicitly.
set(TOOL_DEFINES "")
foreach(tok IN LISTS STANDARD_FLAGS)
    if(tok MATCHES "^-D")
        list(APPEND TOOL_DEFINES "${tok}")
    endif()
endforeach()

# Common -I for tools (add local include only if it exists)
set(TOOL_I_FLAGS "")
if(HAVE_LOCAL_INCLUDE_DIR)
    list(APPEND TOOL_I_FLAGS -I${LOCAL_INCLUDE_DIR})
endif()
foreach(_inc ${EXTERNAL_INCLUDE_DIRS})
    list(APPEND TOOL_I_FLAGS -I${_inc})
endforeach()

# format
if(ALL_SOURCE_FILES OR ALL_HEADER_FILES)
    add_custom_target(format
            COMMAND ${CLANG_FORMAT} --style=file -i ${ALL_SOURCE_FILES} ${ALL_HEADER_FILES}
            COMMENT "Running clang-format"
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
            COMMAND_EXPAND_LISTS)
endif()

# Give clang-tidy an empty DB so it won’t read GCC flags
set(TIDY_EMPTY_DB "${CMAKE_BINARY_DIR}/tidy-empty-db")
file(MAKE_DIRECTORY "${TIDY_EMPTY_DB}")
file(WRITE "${TIDY_EMPTY_DB}/compile_commands.json" "[]")

# Build the -checks arg as a single string
set(TIDY_CHECKS_LIST
        "*"
        "-clang-diagnostic-unused-macros"
        "-llvmlibc-restrict-system-libc-headers"
        "-altera-struct-pack-align"
        "-readability-identifier-length"
        "-altera-unroll-loops"
        "-cppcoreguidelines-init-variables"
        "-cert-err33-c"
        "-modernize-macro-to-enum"
        "-bugprone-easily-swappable-parameters"
        "-clang-analyzer-security.insecureAPI.DeprecatedOrUnsafeBufferHandling"
        "-altera-id-dependent-backward-branch"
        "-concurrency-mt-unsafe"
        "-misc-unused-parameters"
        "-hicpp-signed-bitwise"
        "-google-readability-todo"
        "-cert-msc30-c"
        "-cert-msc50-cpp"
        "-readability-function-cognitive-complexity"
        "-clang-analyzer-security.insecureAPI.strcpy"
        "-cert-env33-c"
        "-android-cloexec-accept"
        "-misc-include-cleaner"
        "-llvm-header-guard"
        "-cppcoreguidelines-macro-to-enum"
        "-llvmlibc-implementation-in-namespace"
        "-llvmlibc-callee-namespace"
)
list(JOIN TIDY_CHECKS_LIST "," TIDY_CHECKS_ARG)

# Language & a standard (helpful when no compile DB is used)
if(_PLANG STREQUAL "C")
    set(TOOL_LANG_ARG -x c)
    set(TIDY_STD --extra-arg=-std=c11)
else()
    set(TOOL_LANG_ARG -x c++)
    set(TIDY_STD --extra-arg=-std=c++20)
endif()

# Inputs
set(TIDY_INPUTS ${ALL_SOURCE_FILES} ${ALL_HEADER_FILES})

if(TIDY_INPUTS)
    # Unique name to avoid clashes across subprojects
    add_custom_target(${PROJECT_NAME}__clang-tidy
            COMMAND ${CLANG_TIDY} ${TIDY_INPUTS}
            -quiet
            --warnings-as-errors=*          # <- no quotes
            -checks=${TIDY_CHECKS_ARG}
            -- ${TOOL_LANG_ARG} ${TIDY_STD} ${TOOL_DEFINES} ${TOOL_I_FLAGS}
            COMMENT "Running clang-tidy (joined -checks; DB disabled)"
            WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
            COMMAND_EXPAND_LISTS
    )
    # Fold into your 'check' target if you like
    if(TARGET check)
        add_dependencies(check ${PROJECT_NAME}__clang-tidy)
    endif()
endif()

# Analyzer enable switch: default ON only if actual compiler is Clang
option(ENABLE_CLANG_ANALYZER "Run clang static analyzer" OFF)
if(CMAKE_${_PLANG}_COMPILER_ID MATCHES ".*Clang")
    set(ENABLE_CLANG_ANALYZER_DEFAULT ON)
else()
    set(ENABLE_CLANG_ANALYZER_DEFAULT OFF)
endif()
if(ENABLE_CLANG_ANALYZER_DEFAULT OR ENABLE_CLANG_ANALYZER)
    if(CLANG_ANALYZER_EXE AND (ALL_SOURCE_FILES OR ALL_HEADER_FILES))
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
    endif()
endif()

# cppcheck (no compiler flags; just sources + defines + includes)
if(_PLANG STREQUAL "C")
    set(CPPCHECK_STD --std=c11)
else()
    set(CPPCHECK_STD --std=c++20)
endif()
if(ALL_SOURCE_FILES OR ALL_HEADER_FILES)
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
endif()

# Aggregate check target
add_custom_target(check ALL)
if(TARGET clang-tidy-check)
    add_dependencies(check clang-tidy-check)
endif()
if(TARGET cppcheck-check)
    add_dependencies(check cppcheck-check)
endif()
if(TARGET clang-analyze)
    add_dependencies(check clang-analyze)
endif()

# =============================
# Install rules (skip headers if no local include dir)
# =============================
if(LIBRARY_TARGETS)
    install(TARGETS ${LIBRARY_TARGETS}
            EXPORT ${PROJECT_NAME}Targets
            LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
            ARCHIVE DESTINATION ${CMAKE_INSTALL_LIBDIR}
            RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
endif()

if(EXECUTABLE_TARGETS)
    install(TARGETS ${EXECUTABLE_TARGETS}
            EXPORT ${PROJECT_NAME}Targets
            RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
endif()

if(HAVE_LOCAL_INCLUDE_DIR)
    install(DIRECTORY ${LOCAL_INCLUDE_DIR}/
            DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
            FILES_MATCHING PATTERN "*.h")
endif()

if(EXECUTABLE_TARGETS OR LIBRARY_TARGETS)
    install(EXPORT ${PROJECT_NAME}Targets
            FILE ${PROJECT_NAME}Targets.cmake
            NAMESPACE ${PROJECT_NAME}::
            DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/${PROJECT_NAME})
endif()
