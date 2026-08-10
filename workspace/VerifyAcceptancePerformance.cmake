foreach(required IN ITEMS POLICY FULL_RECEIPT INCREMENTAL_RECEIPT OUTPUT)
    if(NOT DEFINED ${required} OR "${${required}}" STREQUAL "")
        message(FATAL_ERROR "${required} is required.")
    endif()
endforeach()

foreach(path IN ITEMS "${POLICY}" "${FULL_RECEIPT}" "${INCREMENTAL_RECEIPT}")
    if(NOT EXISTS "${path}")
        message(FATAL_ERROR "Acceptance performance input is missing: ${path}")
    endif()
endforeach()

file(READ "${POLICY}" policy_json)
file(READ "${FULL_RECEIPT}" full_json)
file(READ "${INCREMENTAL_RECEIPT}" incremental_json)

string(JSON policy_schema GET "${policy_json}" schema)
if(NOT policy_schema STREQUAL "p101-acceptance-performance-budget-v1")
    message(FATAL_ERROR "Unsupported acceptance performance policy: ${policy_schema}")
endif()

foreach(prefix IN ITEMS full incremental)
    string(JSON ${prefix}_schema GET "${${prefix}_json}" schema)
    string(JSON ${prefix}_outcome GET "${${prefix}_json}" outcome)
    string(JSON ${prefix}_elapsed_ns GET "${${prefix}_json}" elapsed_ns)
    string(JSON ${prefix}_completed GET "${${prefix}_json}" checks completed)
    string(JSON ${prefix}_reused GET "${${prefix}_json}" cache reused)
    if(NOT ${prefix}_schema STREQUAL "p101-check-graph-receipt-v2")
        message(FATAL_ERROR "${prefix} acceptance receipt has an unsupported schema.")
    endif()
    if(NOT ${prefix}_outcome STREQUAL "clean")
        message(FATAL_ERROR "${prefix} acceptance receipt is not clean.")
    endif()
endforeach()

string(JSON maximum_full_seconds GET "${policy_json}" maximum_full_seconds)
string(JSON maximum_incremental_seconds GET "${policy_json}" maximum_incremental_seconds)
string(JSON minimum_incremental_reused_nodes GET "${policy_json}" minimum_incremental_reused_nodes)
math(EXPR maximum_full_ns "${maximum_full_seconds} * 1000000000")
math(EXPR maximum_incremental_ns "${maximum_incremental_seconds} * 1000000000")

if(full_elapsed_ns GREATER maximum_full_ns)
    message(FATAL_ERROR
            "Full acceptance took ${full_elapsed_ns}ns; budget is ${maximum_full_ns}ns.")
endif()
if(incremental_elapsed_ns GREATER maximum_incremental_ns)
    message(FATAL_ERROR
            "Incremental acceptance took ${incremental_elapsed_ns}ns; budget is "
            "${maximum_incremental_ns}ns.")
endif()
if(incremental_reused LESS minimum_incremental_reused_nodes)
    message(FATAL_ERROR
            "Incremental acceptance reused ${incremental_reused} nodes; budget "
            "requires at least ${minimum_incremental_reused_nodes}.")
endif()
if(NOT incremental_completed EQUAL full_completed)
    message(FATAL_ERROR
            "Incremental acceptance completed ${incremental_completed} checks; "
            "full acceptance completed ${full_completed}.")
endif()

get_filename_component(output_directory "${OUTPUT}" DIRECTORY)
file(MAKE_DIRECTORY "${output_directory}")
file(WRITE "${OUTPUT}.tmp"
        "{\"schema\":\"p101-acceptance-performance-receipt-v1\"," 
        "\"passed\":true,"
        "\"full_elapsed_ns\":${full_elapsed_ns},"
        "\"full_reused_nodes\":${full_reused},"
        "\"incremental_elapsed_ns\":${incremental_elapsed_ns},"
        "\"incremental_reused_nodes\":${incremental_reused},"
        "\"completed_checks\":${full_completed},"
        "\"policy\":{"
        "\"maximum_full_seconds\":${maximum_full_seconds},"
        "\"maximum_incremental_seconds\":${maximum_incremental_seconds},"
        "\"minimum_incremental_reused_nodes\":${minimum_incremental_reused_nodes}},"
        "\"does_not_prove\":\"Budgets bound this host run; they do not predict other hardware or workloads.\"}\n")
file(RENAME "${OUTPUT}.tmp" "${OUTPUT}")
message(STATUS
        "Acceptance performance: full ${full_elapsed_ns}ns; incremental "
        "${incremental_elapsed_ns}ns with ${incremental_reused} reused nodes.")
