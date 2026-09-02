include_guard(GLOBAL)

# CPU build and runtime profiles, read from the one file Python also reads.
#
# `cpu_profiles.json` is tooling data, not a firmware ABI: string(JSON)
# reads it here and the standard json module reads it in novakit, so
# neither side parses the other's syntax and neither holds a copy.

set(NOVA_CPU_PROFILES_JSON "${CMAKE_CURRENT_LIST_DIR}/../src/hal/arch/aarch64/cpu_profiles.json")

# One profile field, or a fatal error naming the profile that lacks it.
function(nova_cpu_profile_field kind profile field out)
    file(READ "${NOVA_CPU_PROFILES_JSON}" _json)
    string(JSON _entry ERROR_VARIABLE _absent GET "${_json}" "${kind}" "${profile}")
    if(_absent STREQUAL "NOTFOUND")
        string(JSON _value GET "${_entry}" "${field}")
        set(${out} "${_value}" PARENT_SCOPE)
        return()
    endif()
    message(FATAL_ERROR "No ${kind} CPU profile '${profile}' in ${NOVA_CPU_PROFILES_JSON}")
endfunction()

# A profile's capability array as a CMake list.
function(nova_cpu_profile_tokens kind profile field out)
    file(READ "${NOVA_CPU_PROFILES_JSON}" _json)
    string(JSON _array GET "${_json}" "${kind}" "${profile}" "${field}")
    string(JSON _count LENGTH "${_array}")
    set(_tokens "")
    if(_count GREATER 0)
        math(EXPR _last "${_count} - 1")
        foreach(_index RANGE ${_last})
            string(JSON _token GET "${_array}" ${_index})
            list(APPEND _tokens "${_token}")
        endforeach()
    endif()
    set(${out} "${_tokens}" PARENT_SCOPE)
endfunction()

# A build runs on a runtime when the runtime provides everything the
# build requires. Element-wise inclusion, never name equality: a
# baseline binary is meant to run on a superset core, and comparing
# profile names would reject exactly that case.
function(nova_validate_cpu_profiles build_profile runtime_profile)
    nova_cpu_profile_tokens(build "${build_profile}" requires _requires)
    nova_cpu_profile_tokens(runtime "${runtime_profile}" provides _provides)
    set(_missing "")
    foreach(_token IN LISTS _requires)
        if(NOT _token IN_LIST _provides)
            list(APPEND _missing "${_token}")
        endif()
    endforeach()
    if(_missing)
        string(REPLACE ";" ", " _named "${_missing}")
        message(FATAL_ERROR
            "A '${build_profile}' build needs ${_named}, which the "
            "'${runtime_profile}' runtime does not provide")
    endif()
endfunction()


# The profiles this configure actually uses: the board's choice unless a
# preset named another. Cached so a build tree records which pairing it
# was configured for, and so novakit can read it back.
function(nova_resolve_cpu_profiles)
    foreach(kind IN ITEMS BUILD RUNTIME)
        if(NOT NOVA_CPU_${kind}_PROFILE)
            set(NOVA_CPU_${kind}_PROFILE "${NOVA_BOARD_${kind}_CPU_PROFILE}")
        endif()
        if(NOT NOVA_CPU_${kind}_PROFILE)
            message(FATAL_ERROR "No ${kind} CPU profile for board '${NOVA_BOARD}'")
        endif()
        set(NOVA_CPU_${kind}_PROFILE "${NOVA_CPU_${kind}_PROFILE}"
            CACHE STRING "${kind} CPU profile" FORCE)
    endforeach()
endfunction()
