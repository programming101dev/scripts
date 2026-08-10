# Shared external-link resolution for p101 C/C++ repositories.
#
# This module owns the portable iconv target and the conversion of logical
# config.cmake link tokens into concrete libraries. Target construction remains
# in the root orchestration file.

if(NOT TARGET p101::iconv)
    add_library(p101::iconv INTERFACE IMPORTED)
endif()

find_library(P101_ICONV_LIB
        NAMES iconv libiconv
        PATHS ${P101_PUBLIC_LINK_DIRS_EXISTING}
        NO_DEFAULT_PATH
)
if(NOT P101_ICONV_LIB)
    find_library(P101_ICONV_LIB NAMES iconv libiconv)
endif()

if(P101_ICONV_LIB)
    set_property(TARGET p101::iconv PROPERTY INTERFACE_LINK_LIBRARIES "${P101_ICONV_LIB}")
    message(STATUS "[iconv] using concrete library: ${P101_ICONV_LIB}")
elseif(CMAKE_SYSTEM_NAME MATCHES "FreeBSD|DragonFly|OpenBSD|NetBSD|Darwin")
    set_property(TARGET p101::iconv PROPERTY INTERFACE_LINK_LIBRARIES "iconv")
    message(STATUS "[iconv] linking as -liconv on ${CMAKE_SYSTEM_NAME}")
else()
    message(STATUS "[iconv] no separate lib needed on this platform")
endif()

function(_p101_resolve_link_items OUT)
    set(_accum "")

    foreach(_L IN LISTS ARGN)
        if(_L STREQUAL "")
            continue()
        endif()

        if(_L STREQUAL "iconv")
            list(APPEND _accum p101::iconv)
            continue()
        endif()

        if(TARGET "${_L}")
            list(APPEND _accum "${_L}")
            continue()
        endif()

        if(P101_IN_TREE_DEPENDENCIES_ONLY AND _L MATCHES "^p101_")
            message(FATAL_ERROR
                    "Logical dependency '${_L}' is not an in-tree target. "
                    "Add its owning repository to the workspace CMake graph "
                    "before the current repository.")
        endif()

        if(IS_ABSOLUTE "${_L}" AND EXISTS "${_L}")
            list(APPEND _accum "${_L}")
            continue()
        endif()

        set(_name "${_L}")
        if(_name MATCHES "^-l(.+)$")
            set(_name "${CMAKE_MATCH_1}")
        endif()

        find_library(_FOUND_LIB
                NAMES "${_name}" "lib${_name}"
                PATHS ${P101_PUBLIC_LINK_DIRS_EXISTING}
                NO_DEFAULT_PATH
        )
        if(NOT _FOUND_LIB)
            find_library(_FOUND_LIB NAMES "${_name}" "lib${_name}")
        endif()

        if(_FOUND_LIB)
            list(APPEND _accum "${_FOUND_LIB}")
        else()
            list(APPEND _accum "${_L}")
        endif()

        unset(_FOUND_LIB CACHE)
        unset(_FOUND_LIB)
    endforeach()

    list(REMOVE_DUPLICATES _accum)
    set(${OUT} "${_accum}" PARENT_SCOPE)
endfunction()
