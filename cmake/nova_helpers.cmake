# Target-definition helpers shared by the NovaVisor build.

# nova_add_component(<name> [SOURCES <src>...] [DEPS <target>...])
#
# Defines the library target for src/components/<name>: an OBJECT
# library when it has sources (so its TUs land in the final ELF),
# INTERFACE when header-only.
#
# Include layout enforces the dependency graph: a component sees the
# foundation trees (nova/, hal/ via the src root) plus its OWN
# include/<name>/ headers; another component's headers resolve only
# when that component is named in DEPS (its include dir propagates
# through the link). An undeclared cross-component include fails to
# compile.
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
    target_include_directories(${name} ${scope}
        ${CMAKE_SOURCE_DIR}/src
        ${CMAKE_SOURCE_DIR}/src/components/${name}/include)
    target_link_libraries(${name} ${scope} cib nova_platform ${ARG_DEPS})
endfunction()

# nova_add_host_test(<name>)
#
# Defines a GTest executable from tests/host/<name>.cpp (tests of the
# foundation trees, no component dependency) and registers it with
# CTest.
function(nova_add_host_test name)
    add_executable(${name} ${CMAKE_SOURCE_DIR}/tests/host/${name}.cpp)
    target_include_directories(${name} PRIVATE ${CMAKE_SOURCE_DIR}/src)
    target_link_libraries(${name} PRIVATE GTest::gtest_main nova_warnings)
    add_test(NAME ${name} COMMAND ${name})
endfunction()

# nova_add_component_test(<component> <name>)
#
# Defines a GTest executable from src/components/<component>/test/
# <name>.cpp — the host-side twin of the component's pure headers. The
# component's include dir is added directly (its cib target only exists
# in the cross build).
function(nova_add_component_test component name)
    add_executable(${name} ${CMAKE_SOURCE_DIR}/src/components/${component}/test/${name}.cpp)
    target_include_directories(${name} PRIVATE
        ${CMAKE_SOURCE_DIR}/src
        ${CMAKE_SOURCE_DIR}/src/components/${component}/include)
    target_link_libraries(${name} PRIVATE GTest::gtest_main nova_warnings)
    add_test(NAME ${name} COMMAND ${name})
endfunction()
