cmake_minimum_required(VERSION 3.14)
project(p101_wrapper_lifecycle_driver C)

set(CMAKE_C_STANDARD 17)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

set(P101_PUBLIC_INCLUDE_DIRS "" CACHE STRING "p101 include directories")
set(P101_PUBLIC_LINK_DIRS "" CACHE STRING "p101 library directories")
separate_arguments(P101_PUBLIC_INCLUDE_DIRS_LIST NATIVE_COMMAND "${P101_PUBLIC_INCLUDE_DIRS}")
separate_arguments(P101_PUBLIC_LINK_DIRS_LIST NATIVE_COMMAND "${P101_PUBLIC_LINK_DIRS}")

set(P101_LIBRARIES
        p101_thread
        p101_process
        p101_sync
        p101_memory
        p101_ipc
        p101_io
        p101_c
        p101_env
        p101_tool_event
        p101_record
        p101_error
)
set(P101_RESOLVED_LIBRARIES "")
foreach(library IN LISTS P101_LIBRARIES)
    unset(P101_LIBRARY CACHE)
    find_library(P101_LIBRARY NAMES ${library}
            PATHS ${P101_PUBLIC_LINK_DIRS_LIST}
            NO_DEFAULT_PATH)
    if(NOT P101_LIBRARY)
        find_library(P101_LIBRARY NAMES ${library})
    endif()
    if(NOT P101_LIBRARY)
        message(FATAL_ERROR "Could not find ${library}")
    endif()
    list(APPEND P101_RESOLVED_LIBRARIES "${P101_LIBRARY}")
endforeach()

add_executable(p101-wrapper-lifecycle-driver driver.c)
target_include_directories(p101-wrapper-lifecycle-driver PRIVATE
        ${P101_PUBLIC_INCLUDE_DIRS_LIST})
target_compile_definitions(p101-wrapper-lifecycle-driver PRIVATE
        _POSIX_C_SOURCE=200809L
        _XOPEN_SOURCE=700)
if(APPLE)
    target_compile_definitions(p101-wrapper-lifecycle-driver PRIVATE _DARWIN_C_SOURCE)
elseif(CMAKE_SYSTEM_NAME STREQUAL "Linux")
    target_compile_definitions(p101-wrapper-lifecycle-driver PRIVATE _GNU_SOURCE)
elseif(CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")
    target_compile_definitions(p101-wrapper-lifecycle-driver PRIVATE _BSD_SOURCE __BSD_VISIBLE)
endif()
target_link_libraries(p101-wrapper-lifecycle-driver PRIVATE
        ${P101_RESOLVED_LIBRARIES}
        pthread)
if(CMAKE_C_COMPILER_ID MATCHES "Clang|GNU")
    target_compile_options(p101-wrapper-lifecycle-driver PRIVATE
            -Wall -Wextra -Werror -pedantic-errors)
endif()
