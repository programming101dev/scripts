cmake_minimum_required(VERSION 3.20)

foreach (_required IN ITEMS SOURCE_DIR BINARY_DIR C_COMPILER CXX_COMPILER)
    if (NOT DEFINED ${_required} OR "${${_required}}" STREQUAL "")
        message(FATAL_ERROR "RunRepositoryTests.cmake requires ${_required}")
    endif ()
endforeach ()

function (_p101_run_test_tree source_directory binary_directory label)
    set(_c_compiler_arg1 "${C_COMPILER_ARG1}")
    set(_cxx_compiler_arg1 "${CXX_COMPILER_ARG1}")

    # The production project may enable only C while its repository tests
    # enable C++ for public-header compatibility (or the inverse).  In that
    # case the outer project has not initialized the other compiler's ARG1.
    # Package-managed Clang installations on macOS can carry a stale default
    # SDK configuration, so apply the same deterministic driver policy to
    # both compilers before a nested test project performs compiler detection.
    if (CMAKE_HOST_APPLE)
        foreach (_language IN ITEMS c cxx)
            if (_language STREQUAL "c")
                set(_compiler "${C_COMPILER}")
                set(_argument_variable _c_compiler_arg1)
            else ()
                set(_compiler "${CXX_COMPILER}")
                set(_argument_variable _cxx_compiler_arg1)
            endif ()
            get_filename_component(_compiler_name "${_compiler}" NAME)
            if (_compiler_name MATCHES "clang"
                    AND "${${_argument_variable}}" STREQUAL "")
                set(${_argument_variable} "--no-default-config")
            endif ()
        endforeach ()
    endif ()

    set(_configure
            "${CMAKE_COMMAND}" -S "${source_directory}" -B "${binary_directory}"
            -U "P101_*_LIBRARY"
            "-DCMAKE_C_COMPILER=${C_COMPILER}"
            "-DCMAKE_CXX_COMPILER=${CXX_COMPILER}"
            "-DCMAKE_POSITION_INDEPENDENT_CODE=ON"
            "-DCMAKE_C_FLAGS=${C_FLAGS}"
            "-DCMAKE_CXX_FLAGS=${CXX_FLAGS}"
            "-DCMAKE_EXE_LINKER_FLAGS=${LINK_FLAGS}"
            "-DP101_PUBLIC_INCLUDE_DIRS=${PUBLIC_INCLUDE_DIRS}"
            "-DP101_PUBLIC_LINK_DIRS=${PUBLIC_LINK_DIRS}"
            "-DP101_SOURCE_BUILD_DIR=${BINARY_DIR}"
            "-DP101_TEST_COVERAGE=OFF")
    if (NOT "${_c_compiler_arg1}" STREQUAL "")
        list(APPEND _configure "-DCMAKE_C_COMPILER_ARG1=${_c_compiler_arg1}")
    endif ()
    if (NOT "${_cxx_compiler_arg1}" STREQUAL "")
        list(APPEND _configure "-DCMAKE_CXX_COMPILER_ARG1=${_cxx_compiler_arg1}")
    endif ()

    message(STATUS "Configuring ${label}: ${binary_directory}")
    execute_process(COMMAND ${_configure} RESULT_VARIABLE _configure_status)
    if (NOT _configure_status EQUAL 0)
        message(FATAL_ERROR "${label} configure failed with exit ${_configure_status}")
    endif ()

    execute_process(
            COMMAND "${CMAKE_COMMAND}" --build "${binary_directory}"
            RESULT_VARIABLE _build_status)
    if (NOT _build_status EQUAL 0)
        message(FATAL_ERROR "${label} build failed with exit ${_build_status}")
    endif ()

    execute_process(
            COMMAND "${CMAKE_CTEST_COMMAND}" --test-dir "${binary_directory}"
                    --output-on-failure
            RESULT_VARIABLE _test_status)
    if (NOT _test_status EQUAL 0)
        message(FATAL_ERROR "${label} tests failed with exit ${_test_status}")
    endif ()
endfunction ()

if (EXISTS "${SOURCE_DIR}/test/CMakeLists.txt")
    _p101_run_test_tree(
            "${SOURCE_DIR}/test"
            "${BINARY_DIR}/repository-tests/root"
            "repository tests")
endif ()

file(GLOB _component_tests RELATIVE "${SOURCE_DIR}"
        "${SOURCE_DIR}/components/*/test/CMakeLists.txt")
foreach (_component_cmake IN LISTS _component_tests)
    get_filename_component(_test_directory "${_component_cmake}" DIRECTORY)
    get_filename_component(_component_directory "${_test_directory}" DIRECTORY)
    get_filename_component(_component_name "${_component_directory}" NAME)
    _p101_run_test_tree(
            "${SOURCE_DIR}/${_test_directory}"
            "${BINARY_DIR}/repository-tests/components/${_component_name}"
            "component ${_component_name} tests")
endforeach ()
