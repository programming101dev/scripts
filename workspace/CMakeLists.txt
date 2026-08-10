cmake_minimum_required(VERSION 3.20)

project(p101_workspace LANGUAGES C)

# This is the dependency graph for the small trusted host toolchain.  It is a
# normal CMake graph: libraries and tools are targets in one build tree, so no
# shell script has to install a library, search old build directories, or guess
# which executable belongs to this run.
set(P101_WORKSPACE_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." CACHE PATH
        "Root of the programming101dev workspace")
get_filename_component(P101_WORKSPACE_ROOT "${P101_WORKSPACE_ROOT}" ABSOLUTE)

set(P101_RUNTIME_ONLY ON CACHE BOOL
        "Build the host toolchain without repository analyzer targets" FORCE)
set(P101_IN_TREE_DEPENDENCIES_ONLY ON CACHE BOOL
        "Do not satisfy host-tool dependencies from stale repository builds" FORCE)
set(P101_WORKSPACE_AGGREGATE ON CACHE INTERNAL
        "Suppress repetitive child-repository configure summaries" FORCE)
set(P101_USE_PROBED_FLAGS OFF CACHE BOOL
        "Use a small deterministic bootstrap flag set" FORCE)
set(P101_ACCEPTANCE_CXX_COMPILER "" CACHE FILEPATH
        "C++ compiler whose completed matrix build is being accepted")
set(P101_ACCEPTANCE_OUTPUT_DIR "${CMAKE_BINARY_DIR}/acceptance" CACHE PATH
        "Persistent exact-evidence cache and acceptance output")
set(P101_ACCEPTANCE_NO_CACHE OFF CACHE BOOL
        "Run every governed acceptance node instead of using exact evidence")
set(P101_VERIFY_INCREMENTAL_ACCEPTANCE ON CACHE BOOL
        "Replay acceptance once and enforce the incremental performance budget")
set(P101_HOST_TOOL_RECEIPT
        "${CMAKE_BINARY_DIR}/host-tool-qualification.json" CACHE FILEPATH
        "Receipt written after the in-tree host tools pass qualification")
set(SANITIZER_LIST "" CACHE STRING
        "The host toolchain is deliberately sanitizer-free" FORCE)

function(p101_add_host_repository relative_path binary_name)
    set(source_dir "${P101_WORKSPACE_ROOT}/${relative_path}")
    if(NOT EXISTS "${source_dir}/CMakeLists.txt")
        message(FATAL_ERROR
                "Required host-tool repository is missing: ${source_dir}")
    endif()
    add_subdirectory("${source_dir}"
            "${CMAKE_CURRENT_BINARY_DIR}/host/${binary_name}")
endfunction()

# Child repositories are intentionally verbose when configured alone.  In the
# aggregate graph that repeats the same compiler and tool details fifteen
# times, so retain errors and warnings while the children are admitted and
# restore normal status output for this graph's own summary.
set(_P101_PARENT_MESSAGE_LOG_LEVEL "${CMAKE_MESSAGE_LOG_LEVEL}")
set(CMAKE_MESSAGE_LOG_LEVEL WARNING)

# Stage 1: the minimal runtime closure needed by the three host-tool programs.
# The order is intentional.  The shared link resolver binds a logical p101_*
# token to an existing in-tree target before it considers an installed library.
p101_add_host_repository(libraries/lib_error lib_error)
p101_add_host_repository(libraries/lib_tool_event lib_tool_event)
p101_add_host_repository(libraries/lib_env lib_env)
p101_add_host_repository(libraries/lib_c lib_c)

p101_add_host_repository(libraries/lib_filesystem lib_filesystem)
p101_add_host_repository(libraries/lib_io lib_io)
p101_add_host_repository(libraries/lib_cli lib_cli)
p101_add_host_repository(libraries/lib_process lib_process)
p101_add_host_repository(libraries/lib_time lib_time)
p101_add_host_repository(libraries/lib_host lib_host)
p101_add_host_repository(libraries/lib_network lib_network)
p101_add_host_repository(libraries/lib_ipc lib_ipc)

p101_add_host_repository(libraries/lib_convert lib_convert)
p101_add_host_repository(libraries/lib_util lib_util)
p101_add_host_repository(libraries/lib_c_facts lib_c_facts)

add_custom_target(p101_host_runtime
        DEPENDS
        p101_error
        p101_record
        p101_tool_event
        p101_env
        p101_c
        p101_filesystem
        p101_io
        p101_cli
        p101_process
        p101_time
        p101_host
        p101_network
        p101_ipc
        p101_convert
        p101_util
        p101_c_facts)

# Stage 2: the actual audit, test, and inspection programs.  They are part of
# this CMake graph rather than prebuilt executables discovered by shell code.
p101_add_host_repository(programs/p101-audit p101-audit)
p101_add_host_repository(programs/p101-test p101-test)
p101_add_host_repository(programs/p101-inspect p101-inspect)

if(_P101_PARENT_MESSAGE_LOG_LEVEL STREQUAL "")
    unset(CMAKE_MESSAGE_LOG_LEVEL)
else()
    set(CMAKE_MESSAGE_LOG_LEVEL "${_P101_PARENT_MESSAGE_LOG_LEVEL}")
endif()
unset(_P101_PARENT_MESSAGE_LOG_LEVEL)

add_custom_target(p101_host_tools
        DEPENDS
        audit_wrappers
        audit_facts
        audit_errors
        audit_modules
        audit_doctor
        test_faults
        test_mutation
        inspect_capture
        p101_event_model
        p101_receipt)

enable_testing()

function(p101_qualify_tool target_name)
    add_test(NAME "host-tool.${target_name}.help"
            COMMAND "$<TARGET_FILE:${target_name}>" --help)
    set_tests_properties("host-tool.${target_name}.help" PROPERTIES
            LABELS "host-tool"
            TIMEOUT 15)

    add_test(NAME "host-tool.${target_name}.rejects-unknown-option"
            COMMAND "$<TARGET_FILE:${target_name}>"
                    --p101-deliberately-invalid-option)
    set_tests_properties(
            "host-tool.${target_name}.rejects-unknown-option" PROPERTIES
            LABELS "host-tool"
            TIMEOUT 15
            WILL_FAIL TRUE)
endfunction()

foreach(host_tool IN ITEMS
        audit_wrappers
        audit_facts
        audit_errors
        audit_modules
        audit_doctor
        test_faults
        test_mutation
        inspect_capture
        p101_event_model
        p101_receipt)
    p101_qualify_tool("${host_tool}")
endforeach()

find_program(P101_BASH_EXECUTABLE NAMES bash REQUIRED)
add_test(NAME host-tool.audit.semantic-regression
        COMMAND "${P101_BASH_EXECUTABLE}"
                "${P101_WORKSPACE_ROOT}/programs/p101-audit/test/test_cli.sh"
                "$<TARGET_FILE:audit_wrappers>"
                "$<TARGET_FILE:audit_facts>")
add_test(NAME host-tool.test.fault-campaign-regression
        COMMAND "${P101_BASH_EXECUTABLE}"
                "${P101_WORKSPACE_ROOT}/programs/p101-test/test/test_cli.sh"
                "$<TARGET_FILE:test_faults>")
add_test(NAME host-tool.inspect.capture-regression
        COMMAND "${P101_BASH_EXECUTABLE}"
                "${P101_WORKSPACE_ROOT}/programs/p101-inspect/test/test_cli.sh"
                "$<TARGET_FILE:inspect_capture>")
set_tests_properties(
        host-tool.audit.semantic-regression
        host-tool.test.fault-campaign-regression
        host-tool.inspect.capture-regression
        PROPERTIES
        LABELS "host-tool"
        WORKING_DIRECTORY "${P101_WORKSPACE_ROOT}"
        TIMEOUT 180)

get_property(_P101_HOST_TESTS DIRECTORY PROPERTY TESTS)
list(LENGTH _P101_HOST_TESTS P101_HOST_TEST_COUNT)
unset(_P101_HOST_TESTS)

set(_P101_HOST_TOOL_TARGETS
        audit_wrappers
        audit_facts
        audit_errors
        audit_modules
        audit_doctor
        test_faults
        test_mutation
        inspect_capture
        p101_event_model
        p101_receipt)
add_custom_command(
        OUTPUT "${P101_HOST_TOOL_RECEIPT}"
        COMMAND "${CMAKE_CTEST_COMMAND}" --test-dir "${CMAKE_BINARY_DIR}"
                --output-on-failure -L host-tool
        COMMAND "${CMAKE_COMMAND}"
                -DOUTPUT=${P101_HOST_TOOL_RECEIPT}
                -DCOMPILER=${CMAKE_C_COMPILER}
                -DTOOL_COUNT=10
                -DTEST_COUNT=${P101_HOST_TEST_COUNT}
                -P "${CMAKE_CURRENT_SOURCE_DIR}/WriteHostToolReceipt.cmake"
        DEPENDS ${_P101_HOST_TOOL_TARGETS}
        USES_TERMINAL
        COMMENT "Qualifying the in-tree p101 host tools"
        VERBATIM)
add_custom_target(p101_tool_qualification
        DEPENDS "${P101_HOST_TOOL_RECEIPT}")
unset(_P101_HOST_TOOL_TARGETS)

# Keep the mature acceptance graph while its policies migrate into C targets.
# The important boundary is already enforced: these checks receive the exact
# receipt executables from this build, never a guessed repository artifact.
if(P101_ACCEPTANCE_CXX_COMPILER STREQUAL "")
    message(STATUS
            "p101_acceptance will qualify host tools only; set "
            "P101_ACCEPTANCE_CXX_COMPILER to run workspace checks as well.")
    add_custom_target(p101_workspace_checks
            DEPENDS p101_tool_qualification)
else()
    add_custom_target(p101_workspace_checks
            COMMAND "${CMAKE_COMMAND}"
                    -DSCRIPTS_ROOT=${CMAKE_CURRENT_SOURCE_DIR}/..
                    -DOUTPUT=${P101_ACCEPTANCE_OUTPUT_DIR}
                    -DC_COMPILER=${CMAKE_C_COMPILER}
                    -DCXX_COMPILER=${P101_ACCEPTANCE_CXX_COMPILER}
                    -DINSPECT_CAPTURE=$<TARGET_FILE:inspect_capture>
                    -DTOOL_RECEIPT=$<TARGET_FILE:p101_receipt>
                    -DAUDIT_WRAPPERS=$<TARGET_FILE:audit_wrappers>
                    -DAUDIT_FACTS=$<TARGET_FILE:audit_facts>
                    -DAUDIT_ERRORS=$<TARGET_FILE:audit_errors>
                    -DAUDIT_MODULES=$<TARGET_FILE:audit_modules>
                    -DAUDIT_DOCTOR=$<TARGET_FILE:audit_doctor>
                    -DTEST_FAULTS=$<TARGET_FILE:test_faults>
                    -DTEST_MUTATION=$<TARGET_FILE:test_mutation>
                    -DEVENT_MODEL=$<TARGET_FILE:p101_event_model>
                    -DNO_CACHE=${P101_ACCEPTANCE_NO_CACHE}
                    -DVERIFY_INCREMENTAL=${P101_VERIFY_INCREMENTAL_ACCEPTANCE}
                    -DPERFORMANCE_POLICY=${CMAKE_CURRENT_SOURCE_DIR}/../contracts/p101-performance-budget.json
                    -P "${CMAKE_CURRENT_SOURCE_DIR}/RunAcceptance.cmake"
            DEPENDS p101_tool_qualification
            USES_TERMINAL
            COMMENT "Running strict workspace acceptance with qualified host tools")
endif()

# This is the stable target that setup/update entry points invoke.
add_custom_target(p101_acceptance
        DEPENDS p101_workspace_checks)

message(STATUS "")
message(STATUS "p101 workspace host graph")
message(STATUS "  C compiler        = ${CMAKE_C_COMPILER}")
message(STATUS "  C++ evidence      = ${P101_ACCEPTANCE_CXX_COMPILER}")
message(STATUS "  runtime libraries = 16 targets from 15 repositories")
message(STATUS "  host tools        = 10 targets from 3 repositories")
message(STATUS "  acceptance output = ${P101_ACCEPTANCE_OUTPUT_DIR}")
message(STATUS "")
