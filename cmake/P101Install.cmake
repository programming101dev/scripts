# Shared install policy for p101 C/C++ repositories.
#
# Admitted inputs are the target/header lists assembled by the root
# CMakeLists.txt. An executable may set <target>_INSTALL to OFF when it is a
# build-only teaching example. This module owns installation layout only; it
# does not decide which targets or headers belong to a repository.

if(_ALL_HEADERS_FOR_INSTALL)
    foreach(_h IN LISTS _ALL_HEADERS_FOR_INSTALL)
        if(NOT IS_ABSOLUTE "${_h}")
            set(_habspath "${CMAKE_CURRENT_SOURCE_DIR}/${_h}")
        else()
            set(_habspath "${_h}")
        endif()
        string(FIND "${_habspath}" "${PUBLIC_INC_DIR}/" _pfx)
        if(_pfx EQUAL 0)
            file(RELATIVE_PATH _rel_inside_inc "${PUBLIC_INC_DIR}" "${_habspath}")
            get_filename_component(_dest_subdir "${_rel_inside_inc}" DIRECTORY)
        else()
            set(_dest_subdir "extra")
        endif()
        install(FILES "${_habspath}" DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}/${_dest_subdir}")
    endforeach()
endif()

foreach(_lib IN LISTS LIBRARY_TARGETS)
    install(TARGETS ${_lib}
            LIBRARY DESTINATION "${CMAKE_INSTALL_LIBDIR}"
            ARCHIVE DESTINATION "${CMAKE_INSTALL_LIBDIR}"
            RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}")
endforeach()

foreach(_exe IN LISTS EXECUTABLE_TARGETS)
    set(_p101_install_executable TRUE)
    if(DEFINED ${_exe}_INSTALL)
        set(_p101_install_executable "${${_exe}_INSTALL}")
    endif()
    if(_p101_install_executable)
        install(TARGETS ${_exe}
                RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}")
    endif()
endforeach()
