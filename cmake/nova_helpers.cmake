# Target-definition helpers shared by the NovaVisor build.

# nova_python_module(<out-var> <module>)
#
# Resolve the argv that runs an automation module out of scripts/. A
# custom command then names the module the same way the CLI and the
# tests do, so the package has one reachable form instead of a file path
# per caller.
function(nova_python_module out_var module)
    set(${out_var}
        ${CMAKE_COMMAND} -E env PYTHONPATH=${CMAKE_SOURCE_DIR}/scripts
        python3 -m novakit.${module}
        PARENT_SCOPE)
endfunction()

# nova_component_dir(<out-var> <name>)
#
# Resolve a component's directory from its name alone. Components are
# grouped by role under src/components/<group>/, but names are unique
# across groups, so a project manifest, a DEPS entry and a test
# registration all keep naming just the component.
function(nova_component_dir out_var name)
    file(GLOB matches "${CMAKE_SOURCE_DIR}/src/components/*/${name}/CMakeLists.txt")
    list(LENGTH matches count)
    if(count EQUAL 0)
        message(FATAL_ERROR "Unknown component '${name}': no src/components/<group>/${name}/CMakeLists.txt")
    elseif(NOT count EQUAL 1)
        message(FATAL_ERROR "Component name '${name}' is ambiguous: ${matches}")
    endif()
    get_filename_component(dir "${matches}" DIRECTORY)
    set(${out_var} "${dir}" PARENT_SCOPE)
endfunction()

# nova_add_component(<name> [SOURCES <src>...] [DEPS <target>...])
#
# Defines the library target for the calling component directory: an
# OBJECT library when it has sources (so its TUs land in the final ELF),
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
        ${CMAKE_CURRENT_SOURCE_DIR}/include)
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

# nova_add_component_test(<component> <name> [PEERS <component>...])
#
# Defines a GTest executable from the component's test/<name>.cpp — the
# host-side twin of its pure headers. Include dirs are added directly
# (the cib targets only exist in the cross build), so a model that
# spans two components names the other one in PEERS.
function(nova_add_component_test component name)
    cmake_parse_arguments(ARG "" "" "PEERS" ${ARGN})
    nova_component_dir(dir "${component}")
    add_executable(${name} ${dir}/test/${name}.cpp)
    target_include_directories(${name} PRIVATE ${CMAKE_SOURCE_DIR}/src ${dir}/include)
    foreach(peer IN LISTS ARG_PEERS)
        nova_component_dir(peer_dir "${peer}")
        target_include_directories(${name} PRIVATE ${peer_dir}/include)
    endforeach()
    target_link_libraries(${name} PRIVATE GTest::gtest_main nova_warnings)
    add_test(NAME ${name} COMMAND ${name})
endfunction()
