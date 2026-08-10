if(NOT DEFINED OUTPUT OR OUTPUT STREQUAL "")
    message(FATAL_ERROR "Host-tool receipt output is required.")
endif()

get_filename_component(output_directory "${OUTPUT}" DIRECTORY)
file(MAKE_DIRECTORY "${output_directory}")
string(TIMESTAMP completed_at "%Y-%m-%dT%H:%M:%SZ" UTC)
string(REPLACE "\\" "\\\\" compiler_json "${COMPILER}")
string(REPLACE "\"" "\\\"" compiler_json "${compiler_json}")

set(temporary "${OUTPUT}.tmp")
file(WRITE "${temporary}"
        "{\"schema\":\"p101-host-tool-qualification-v1\","
        "\"passed\":true,"
        "\"compiler\":\"${compiler_json}\","
        "\"tool_count\":${TOOL_COUNT},"
        "\"test_count\":${TEST_COUNT},"
        "\"completed_at\":\"${completed_at}\","
        "\"does_not_prove\":\"Qualification admits only the declared host-tool smoke and semantic regression tests. Workspace policy is evaluated by p101_acceptance.\"}\n")
file(RENAME "${temporary}" "${OUTPUT}")
