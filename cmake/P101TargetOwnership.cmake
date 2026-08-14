include_guard(GLOBAL)

# Map a logical public target to the repository that owns its artifacts. Most
# targets use the conventional p101_name -> lib_name mapping, while a few
# coherent repositories intentionally publish more than one focused target.
function(_p101_workspace_target_owner OUT TARGET_NAME)
    if(NOT TARGET_NAME MATCHES "^p101_([A-Za-z0-9_]+)$")
        set(${OUT} "" PARENT_SCOPE)
        return()
    endif()

    set(_p101_owner "lib_${CMAKE_MATCH_1}")
    if(TARGET_NAME STREQUAL "p101_thread" OR
            TARGET_NAME STREQUAL "p101_sync")
        set(_p101_owner "lib_concurrency")
    elseif(TARGET_NAME STREQUAL "p101_math" OR
            TARGET_NAME STREQUAL "p101_random")
        set(_p101_owner "lib_numeric")
    elseif(TARGET_NAME STREQUAL "p101_locale")
        set(_p101_owner "lib_text")
    endif()

    set(${OUT} "${_p101_owner}" PARENT_SCOPE)
endfunction()

# Return the transitive include-directory closure for a logical target. Each
# repository's config.cmake is evaluated inside this function scope, making the
# target's declared dependency list the source of truth without leaking the
# dependency repository's project settings into its consumer.
function(_p101_workspace_target_include_closure OUT WORKSPACE_ROOT TARGET_NAME)
    set(_p101_pending "${TARGET_NAME}")
    set(_p101_seen "")
    set(_p101_dirs "")

    while(_p101_pending)
        list(POP_FRONT _p101_pending _p101_current)
        list(FIND _p101_seen "${_p101_current}" _p101_seen_index)
        if(NOT _p101_seen_index EQUAL -1)
            continue()
        endif()
        list(APPEND _p101_seen "${_p101_current}")

        _p101_workspace_target_owner(_p101_owner "${_p101_current}")
        if(_p101_owner STREQUAL "")
            continue()
        endif()

        set(_p101_repo "${WORKSPACE_ROOT}/libraries/${_p101_owner}")
        if(IS_DIRECTORY "${_p101_repo}/include")
            list(APPEND _p101_dirs "${_p101_repo}/include")
        endif()

        set(_p101_config "${_p101_repo}/config.cmake")
        if(EXISTS "${_p101_config}")
            set(_p101_dependency_var "${_p101_current}_LINK_LIBRARIES")
            unset(${_p101_dependency_var})
            include("${_p101_config}")
            foreach(_p101_dependency IN LISTS ${_p101_dependency_var})
                if(_p101_dependency MATCHES "^p101_[A-Za-z0-9_]+$")
                    list(APPEND _p101_pending "${_p101_dependency}")
                endif()
            endforeach()
        endif()
    endwhile()

    list(REMOVE_DUPLICATES _p101_dirs)
    set(${OUT} "${_p101_dirs}" PARENT_SCOPE)
endfunction()
