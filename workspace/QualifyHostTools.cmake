foreach(required OUTPUT COMPILER TOOL_COUNT TEST_COUNT CTEST_COMMAND
        BUILD_DIRECTORY RECEIPT_WRITER)
    if(NOT DEFINED ${required} OR "${${required}}" STREQUAL "")
        message(FATAL_ERROR "${required} is required for host-tool qualification")
    endif()
endforeach()

set(input_identity "compiler=${COMPILER}\ntool_count=${TOOL_COUNT}\ntest_count=${TEST_COUNT}\n")
if(TOOL_COUNT GREATER 0)
    math(EXPR last_tool_index "${TOOL_COUNT} - 1")
    foreach(tool_index RANGE 0 ${last_tool_index})
        set(tool_variable "TOOL_${tool_index}")
        if(NOT DEFINED ${tool_variable} OR NOT EXISTS "${${tool_variable}}")
            message(FATAL_ERROR "Qualified host tool ${tool_index} is missing")
        endif()
        file(SHA256 "${${tool_variable}}" tool_sha256)
        string(APPEND input_identity
                "tool=${${tool_variable}}\nsha256=${tool_sha256}\n")
    endforeach()
endif()
string(SHA256 input_sha256 "${input_identity}")

set(receipt_is_current FALSE)
if(EXISTS "${OUTPUT}")
    file(READ "${OUTPUT}" existing_receipt)
    string(FIND "${existing_receipt}"
            "\"input_sha256\":\"${input_sha256}\"" input_match)
    if(NOT input_match EQUAL -1)
        set(receipt_is_current TRUE)
    endif()
endif()

if(receipt_is_current)
    message(STATUS "Host-tool qualification is current")
    return()
endif()

message(STATUS "Qualifying the in-tree p101 host tools")
execute_process(
        COMMAND "${CTEST_COMMAND}" --test-dir "${BUILD_DIRECTORY}"
                --output-on-failure -L host-tool
        RESULT_VARIABLE qualification_status
        COMMAND_ECHO STDOUT)
if(NOT qualification_status EQUAL 0)
    message(FATAL_ERROR
            "Host-tool qualification failed with exit ${qualification_status}")
endif()

set(INPUT_SHA256 "${input_sha256}")
include("${RECEIPT_WRITER}")
