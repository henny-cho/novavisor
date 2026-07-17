# Target-definition helpers shared by the NovaVisor build.

# nova_add_component(<name> [SOURCES <src>...] [DEPS <target>...])
#
# Defines the library target for components/<name>: an OBJECT library when it
# has sources (so its TUs land in the final ELF), INTERFACE when header-only.
# Every component exposes headers relative to the repo root and depends on
# cib; warnings apply only to the component's own TUs.
function(nova_add_component name)
    cmake_parse_arguments(ARG "" "" "SOURCES;DEPS" ${ARGN})
    if(ARG_SOURCES)
        add_library(${name} OBJECT ${ARG_SOURCES})
        set(scope PUBLIC)
        target_link_libraries(${name} PRIVATE nova_warnings)
    else()
        add_library(${name} INTERFACE)
        set(scope INTERFACE)
    endif()
    target_include_directories(${name} ${scope} ${CMAKE_SOURCE_DIR})
    target_link_libraries(${name} ${scope} cib ${ARG_DEPS})
endfunction()

# nova_add_host_test(<name>)
#
# Defines a GTest executable from tests/host/<name>.cpp and registers it
# with CTest.
function(nova_add_host_test name)
    add_executable(${name} ${CMAKE_SOURCE_DIR}/tests/host/${name}.cpp)
    target_include_directories(${name} PRIVATE ${CMAKE_SOURCE_DIR})
    target_link_libraries(${name} PRIVATE GTest::gtest_main nova_warnings)
    add_test(NAME ${name} COMMAND ${name})
endfunction()
