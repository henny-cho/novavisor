# Target-definition helpers shared by the NovaVisor build.

# Which interpreter runs the generators below. The automation hands its
# own over when it configures, since the generators import the package
# it is running from; a tree configured by hand gets python3 from PATH,
# and a generator needing more than that says so.
set(NOVA_PYTHON "python3" CACHE FILEPATH "Python interpreter for build-graph generators")

# nova_python_module(<out-var> <module>)
#
# Resolve the argv that runs an automation module out of scripts/. A
# custom command then names the module the same way the CLI and the
# tests do, so the package has one reachable form instead of a file path
# per caller.
function(nova_python_module out_var module)
    set(${out_var}
        ${CMAKE_COMMAND} -E env PYTHONPATH=${CMAKE_SOURCE_DIR}/scripts
        ${NOVA_PYTHON} -m novakit.${module}
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

# nova_add_test(<name> [PEERS <component>...])
#
# Defines a GTest executable from src/**/test/<name>.cpp — the host-side
# twin of pure headers, wherever those headers live. Resolved by name
# alone, the way a component is: a test lives beside what it tests, and
# a registration should not repeat the path that already says so.
#
# What a test may include follows from where it sits: the source root
# always, plus the include/ of the directory it is nested in when there
# is one. Include dirs are added directly because the cib targets only
# exist in the cross build, so a model spanning two components names the
# other in PEERS — the one thing the location cannot state.
function(nova_add_test name)
    cmake_parse_arguments(ARG "" "" "PEERS" ${ARGN})
    file(GLOB_RECURSE matches "${CMAKE_SOURCE_DIR}/src/${name}.cpp")
    list(LENGTH matches count)
    if(count EQUAL 0)
        message(FATAL_ERROR "Unknown test '${name}': no src/**/test/${name}.cpp")
    elseif(NOT count EQUAL 1)
        message(FATAL_ERROR "Test name '${name}' is ambiguous: ${matches}")
    endif()
    add_executable(${name} ${matches})
    target_include_directories(${name} PRIVATE ${CMAKE_SOURCE_DIR}/src)
    get_filename_component(test_dir "${matches}" DIRECTORY)
    get_filename_component(owner "${test_dir}" DIRECTORY)
    if(IS_DIRECTORY "${owner}/include")
        target_include_directories(${name} PRIVATE ${owner}/include)
    endif()
    foreach(peer IN LISTS ARG_PEERS)
        nova_component_dir(peer_dir "${peer}")
        target_include_directories(${name} PRIVATE ${peer_dir}/include)
    endforeach()
    target_link_libraries(${name} PRIVATE GTest::gtest_main nova_warnings)
    add_test(NAME ${name} COMMAND ${name})
endfunction()
