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

# Optional: apply fixes in place. -DFIX=ON turns the check pass into a fix pass
# using the SAME full check set / args / DB. --fix-errors also applies fixes when
# a TU still has errors, so a student's in-progress code still gets cleaned up.
if(DEFINED FIX AND FIX)
  list(APPEND _args --fix --fix-errors)
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
    # In fix mode, a non-zero exit just means diagnostics remained after fixing
    # what it could — that is not a failure of the format action.
    if(NOT (DEFINED FIX AND FIX))
      set(_fail 1)
    endif()
  endif()
endforeach()

if(_fail)
  message(FATAL_ERROR "clang-tidy reported failures")
endif()
