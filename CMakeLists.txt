cmake_minimum_required(VERSION 3.20)
set(CMAKE_TRY_COMPILE_TARGET_TYPE "STATIC_LIBRARY")
project(NovaVisor LANGUAGES C CXX ASM)

# ==============================================================================
# Global C++ & Bare-metal Configuration
# ==============================================================================
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Universal warning flags
add_compile_options(
    $<$<COMPILE_LANGUAGE:CXX>:-Wall>
    $<$<COMPILE_LANGUAGE:CXX>:-Wextra>
    $<$<COMPILE_LANGUAGE:CXX>:-Wpedantic>
    $<$<COMPILE_LANGUAGE:CXX>:-Werror>
)

# Zero-cost abstraction rules: No RTTI, No Exceptions (bare-metal only)
if(CMAKE_CROSSCOMPILING)
    add_compile_options(
        $<$<COMPILE_LANGUAGE:CXX>:-fno-rtti>
        $<$<COMPILE_LANGUAGE:CXX>:-fno-exceptions>
    )
endif()

# Enable exporting of compile_commands.json for clang-tidy/clangd via VSCode
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ==============================================================================
# Clang-Tidy Configuration (Expert Cross-Compile Support)
# ==============================================================================
option(ENABLE_CLANG_TIDY "Enable clang-tidy during build" OFF)

if(ENABLE_CLANG_TIDY)
    find_program(CLANG_TIDY_EXE NAMES clang-tidy)
    if(CLANG_TIDY_EXE)
        set(CMAKE_CXX_CLANG_TIDY ${CLANG_TIDY_EXE})

        # When cross-compiling for AArch64 bare-metal, clang-tidy needs extra help
        if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU" AND CMAKE_SYSTEM_PROCESSOR STREQUAL "aarch64")
            list(APPEND CMAKE_CXX_CLANG_TIDY --extra-arg=--target=aarch64-none-elf)

            # Use implicit includes from the GCC compiler, but exclude GCC's builtin headers
            # to let Clang use its own resource headers for intrinsics/builtins.
            set(IMPLICIT_INCLUDES ${CMAKE_CXX_IMPLICIT_INCLUDE_DIRECTORIES})

            foreach(inc_path ${IMPLICIT_INCLUDES})
                if(inc_path MATCHES ".*lib/gcc.*")
                    continue()
                endif()
                list(APPEND CMAKE_CXX_CLANG_TIDY "--extra-arg=-isystem${inc_path}")
            endforeach()

            # Print for debugging
            message(STATUS "Clang-Tidy enabled with target: aarch64-none-elf")
        endif()
    else()
        message(WARNING "clang-tidy not found, skipping linting")
    endif()
endif()

# ==============================================================================
# CMake Package Manager (CPM) — v0.42.1
# Must be included before any FetchContent/CPM dependency so that CIB's
# bundled get_cpm.cmake (pinned at v0.40.2) finds CPM already loaded and
# skips its own download via CPM's double-include guard.
# ==============================================================================
include(cmake/get_cpm.cmake)

# Pre-register CIB transitive dependencies to suppress false-positive CPM
# version-mismatch warnings.
#
# CIB pins cpp-baremetal-concurrency@536bdd7 and cpp-std-extensions@c932eba.
# Its transitive dep cpp-baremetal-senders-and-receivers later requests them
# at 63230eb and 2b7917f. CPM treats git hash prefixes as integers, so it
# warns "63230 > 536" — even though 536bdd7 is the NEWER commit in git
# history. Pre-registering both packages with VERSION 99999 ensures all
# subsequent CPM requests satisfy the "included >= requested" check silently.
CPMAddPackage(
    NAME cpp-baremetal-concurrency
    GITHUB_REPOSITORY intel/cpp-baremetal-concurrency
    GIT_TAG 536bdd7
    VERSION 99999
)
CPMAddPackage(
    NAME cpp-std-extensions
    GITHUB_REPOSITORY intel/cpp-std-extensions
    GIT_TAG c932eba
    VERSION 99999
)

# ==============================================================================
# Fetch Dependencies
# ==============================================================================
include(FetchContent)

# 1. Intel compile-time-init-build (cib)
message(STATUS "Fetching Intel cib library...")
FetchContent_Declare(
    cib
    GIT_REPOSITORY https://github.com/intel/compile-time-init-build.git
    GIT_TAG main # or specific release hash
)
FetchContent_MakeAvailable(cib)

# 2. Embedded Template Library (ETL)
message(STATUS "Fetching ETL library...")
FetchContent_Declare(
    etl
    GIT_REPOSITORY https://github.com/ETLCPP/etl.git
    GIT_TAG master
)
FetchContent_MakeAvailable(etl)

# 3. Google Pigweed (selective module integration) — cross-build only, guarded internally
include(cmake/pigweed_setup.cmake)

# ==============================================================================
# Target Definitions — bare-metal ELF (cross) vs host GTest (native)
# ==============================================================================

if(CMAKE_CROSSCOMPILING)

    set(SOURCES
        hal/armv8_aarch64/boot.S
        hal/armv8_aarch64/vec.S
        projects/qemu_virt_arm64/main.cpp
        components/nova_panic/src/nova_panic.cpp
        components/nova_panic/src/pw_assert_backend.cc
        components/trap_handler/src/trap_handler.cpp
    )

    add_executable(novavisor.elf ${SOURCES})

    # Link CIB (all sub-libraries), ETL, and Pigweed tier1
    target_link_libraries(novavisor.elf PRIVATE cib etl pw_tier1)

    # Project root as include base so all headers use absolute-from-root paths
    # e.g. #include "components/foo/include/foo.hpp"
    target_include_directories(novavisor.elf PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

    # Use STDX freestanding mode: bypasses {fmt} dependency, uses bare-metal safe
    # compile-time string formatting from cpp-std-extensions
    target_compile_definitions(novavisor.elf PRIVATE STDX_FREESTANDING)

    # Add Linker script and bare-metal linking rules
    target_link_options(novavisor.elf PRIVATE
        -T ${CMAKE_CURRENT_SOURCE_DIR}/hal/armv8_aarch64/linker.ld
        -nostartfiles
        -nostdlib
        -Wl,--gc-sections
        # Bare-metal C runtime builtins (strlen, memcpy, etc.) from newlib-nano.
        # No dynamic allocation is introduced — we do not call malloc/free.
        -lgcc
        -lc
    )

    # Custom command to generate raw flat binary required by some QEMU boots
    add_custom_command(TARGET novavisor.elf POST_BUILD
        COMMAND ${CMAKE_OBJCOPY} -O binary novavisor.elf novavisor.bin
        COMMENT "Generating flat binary novavisor.bin"
    )

else()
    # ===========================================================================
    # Host GTest Target (x86_64, native build)
    # Tests header-only utilities that depend only on <cstdint>: esr.hpp,
    # trap_context.hpp.
    # ===========================================================================
    enable_testing()

    find_package(GTest QUIET)
    if(NOT GTest_FOUND)
        CPMAddPackage(
            NAME googletest
            GITHUB_REPOSITORY google/googletest
            GIT_TAG v1.14.0
            OPTIONS "INSTALL_GTEST OFF" "BUILD_GMOCK OFF"
        )
    endif()

    add_executable(esr_parse_test tests/host/esr_parse_test.cpp)
    target_include_directories(esr_parse_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_link_libraries(esr_parse_test PRIVATE GTest::gtest_main)
    add_test(NAME esr_parse_test COMMAND esr_parse_test)

endif() # CMAKE_CROSSCOMPILING
