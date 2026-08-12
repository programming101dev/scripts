set(check_command
        "${SCRIPTS_ROOT}/check-after-update-all.sh"
        -c "${C_COMPILER}"
        -x "${CXX_COMPILER}"
        -o "${OUTPUT}")
if(NO_CACHE)
    list(APPEND check_command --no-cache)
elseif(EXISTS "${OUTPUT}/receipt.json")
    list(APPEND check_command --resume)
endif()

set(tool_environment
        "P101_INSPECT_CAPTURE=${INSPECT_CAPTURE}"
        "P101_INSPECT=${P101_INSPECT}"
        "P101_TOOL_RECEIPT=${TOOL_RECEIPT}"
        "P101_AUDIT_WRAPPERS=${AUDIT_WRAPPERS}"
        "P101_AUDIT_FACTS=${AUDIT_FACTS}"
        "P101_AUDIT_ERRORS=${AUDIT_ERRORS}"
        "P101_AUDIT_MODULES=${AUDIT_MODULES}"
        "P101_AUDIT_DOCTOR=${AUDIT_DOCTOR}"
        "P101_TEST_FAULTS=${TEST_FAULTS}"
        "P101_TEST_MUTATION=${TEST_MUTATION}"
        "P101_EVENT_MODEL=${EVENT_MODEL}")

execute_process(
        COMMAND "${CMAKE_COMMAND}" -E env
                ${tool_environment}
                ${check_command}
        WORKING_DIRECTORY "${SCRIPTS_ROOT}"
        RESULT_VARIABLE acceptance_status)
if (NOT acceptance_status EQUAL 0)
    message(FATAL_ERROR
            "Strict workspace acceptance failed with exit ${acceptance_status}.")
endif()

if(VERIFY_INCREMENTAL)
    set(incremental_output "${OUTPUT}/incremental")
    file(REMOVE_RECURSE "${incremental_output}")
    file(MAKE_DIRECTORY "${incremental_output}")
    configure_file("${OUTPUT}/receipt.json" "${OUTPUT}/full-receipt.json" COPYONLY)
    configure_file("${OUTPUT}/summary.md" "${OUTPUT}/full-summary.md" COPYONLY)
    configure_file("${OUTPUT}/profile.md" "${OUTPUT}/full-profile.md" COPYONLY)
    execute_process(
            COMMAND "${CMAKE_COMMAND}" -E env
                    ${tool_environment}
                    "${SCRIPTS_ROOT}/check-after-update-all.sh"
                    -c "${C_COMPILER}"
                    -x "${CXX_COMPILER}"
                    -o "${OUTPUT}"
                    --resume
            WORKING_DIRECTORY "${SCRIPTS_ROOT}"
            RESULT_VARIABLE incremental_status)
    configure_file("${OUTPUT}/receipt.json" "${incremental_output}/receipt.json" COPYONLY)
    configure_file("${OUTPUT}/summary.md" "${incremental_output}/summary.md" COPYONLY)
    configure_file("${OUTPUT}/profile.md" "${incremental_output}/profile.md" COPYONLY)
    configure_file("${OUTPUT}/full-receipt.json" "${OUTPUT}/receipt.json" COPYONLY)
    configure_file("${OUTPUT}/full-summary.md" "${OUTPUT}/summary.md" COPYONLY)
    configure_file("${OUTPUT}/full-profile.md" "${OUTPUT}/profile.md" COPYONLY)
    if(NOT incremental_status EQUAL 0)
        message(FATAL_ERROR
                "Incremental workspace acceptance failed with exit "
                "${incremental_status}.")
    endif()
    execute_process(
            COMMAND "${CMAKE_COMMAND}"
                    -DPOLICY=${PERFORMANCE_POLICY}
                    -DFULL_RECEIPT=${OUTPUT}/full-receipt.json
                    -DINCREMENTAL_RECEIPT=${incremental_output}/receipt.json
                    -DOUTPUT=${OUTPUT}/performance-receipt.json
                    -P "${CMAKE_CURRENT_LIST_DIR}/VerifyAcceptancePerformance.cmake"
            RESULT_VARIABLE performance_status)
    if(NOT performance_status EQUAL 0)
        message(FATAL_ERROR
                "Acceptance performance budget failed with exit "
                "${performance_status}.")
    endif()
endif()
