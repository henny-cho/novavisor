# Clang-Tidy integration for cross-compiled bare-metal AArch64.
#
# Gated by ENABLE_CLANG_TIDY (option defined by caller). When cross-compiling
# with GCC, we retarget clang-tidy to aarch64-none-elf and feed it GCC's
# implicit include dirs — minus GCC's builtin headers, which must come from
# Clang's own resource dir for intrinsic declarations to resolve.

option(ENABLE_CLANG_TIDY "Enable clang-tidy during build" OFF)

if(NOT ENABLE_CLANG_TIDY)
    return()
endif()

find_program(CLANG_TIDY_EXE NAMES clang-tidy)
if(NOT CLANG_TIDY_EXE)
    message(WARNING "clang-tidy not found, skipping linting")
    return()
endif()

set(CMAKE_CXX_CLANG_TIDY ${CLANG_TIDY_EXE})

if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU" AND CMAKE_SYSTEM_PROCESSOR STREQUAL "aarch64")
    list(APPEND CMAKE_CXX_CLANG_TIDY --extra-arg=--target=aarch64-none-elf)

    foreach(inc_path ${CMAKE_CXX_IMPLICIT_INCLUDE_DIRECTORIES})
        if(inc_path MATCHES ".*lib/gcc.*")
            continue()
        endif()
        list(APPEND CMAKE_CXX_CLANG_TIDY "--extra-arg=-isystem${inc_path}")
    endforeach()

    message(STATUS "Clang-Tidy enabled with target: aarch64-none-elf")
endif()
