cmake_minimum_required(VERSION 3.20)
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

# Clang-Tidy integration (cross-compile aware).
include(cmake/clang_tidy.cmake)

# CMake Package Manager (CPM) — v0.42.1. Must be included before any
# FetchContent/CPM dependency so CIB's bundled get_cpm.cmake (pinned at
# v0.40.2) finds CPM already loaded and skips its own download.
include(cmake/get_cpm.cmake)

# Silence CPM version-mismatch warnings from CIB's transitive deps.
include(cmake/cib_cpm_pin.cmake)
nova_pin_cib_transitive_packages()

# ==============================================================================
# Fetch Dependencies
# ==============================================================================
include(FetchContent)

# 1. Intel compile-time-init-build (cib)
message(STATUS "Fetching Intel cib library...")
FetchContent_Declare(
    cib
    GIT_REPOSITORY https://github.com/intel/compile-time-init-build.git
    # Pinned to the commit before cib main adopted add_versioned_package(URI ...)
    # keyword syntax, which the bundled cicd-repo-infrastructure@3e2bef0 does not
    # yet forward to CPM — causing CPM NAME inference to fail for boost_mp11.
    GIT_TAG 488d892
)
FetchContent_MakeAvailable(cib)

# cib transitively adds {fmt} as a CPM dependency. We never link it
# (STDX_FREESTANDING bypasses the stdx::cts_string → fmt path) but its
# format.cc still ends up in the default build graph, where bare-metal
# libstdc++ headers fail at -O0 because __throw_bad_cast/__throw_length_error
# are referenced from template bodies that only survive dead-code elimination
# at -O1+. Excluding fmt from the default build avoids this entirely.
if(TARGET fmt)
    set_target_properties(fmt PROPERTIES EXCLUDE_FROM_ALL TRUE)
endif()

# 2. Embedded Template Library (ETL)
# Pinned to a specific commit for reproducible builds. Upstream tag 20.47.1
# is the nearest tagged release; this commit is 1 ahead of that tag.
message(STATUS "Fetching ETL library...")
FetchContent_Declare(
    etl
    GIT_REPOSITORY https://github.com/ETLCPP/etl.git
    GIT_TAG 3e4d41ca57de475f4b769320308e80a103c8f5d6
)
FetchContent_MakeAvailable(etl)

# ==============================================================================
# Target Definitions — bare-metal ELF (cross) vs host GTest (native)
# ==============================================================================

if(CMAKE_CROSSCOMPILING)

    # HAL + components (each owns its own CMakeLists.txt).
    add_subdirectory(hal/armv8_aarch64)
    add_subdirectory(hal/board_qemu_virt)
    add_subdirectory(components/nova_panic)
    add_subdirectory(components/trap_handler)
    add_subdirectory(components/hal_init)
    add_subdirectory(components/boot_msg)
    add_subdirectory(components/idle)
    add_subdirectory(components/core_mmu)
    add_subdirectory(components/core_vcpu)
    add_subdirectory(components/demo_hvc)

    add_executable(novavisor.elf projects/qemu_virt_arm64/main.cpp)

    target_link_libraries(novavisor.elf PRIVATE
        # Components and HAL
        hal_armv8_aarch64
        hal_board_qemu_virt
        hal_init
        boot_msg
        nova_panic
        trap_handler
        core_mmu
        core_vcpu
        demo_hvc
        # Frameworks
        cib etl
        # Bare-metal C runtime (strlen, memcpy, etc.) from newlib.
        # --start-group/--end-group resolves libgcc↔libc circular deps —
        # otherwise strlen references from char_traits::length only resolve
        # at -O2 where the call is inlined. No dynamic allocation introduced.
        -Wl,--start-group -lgcc -lc -Wl,--end-group
    )

    target_include_directories(novavisor.elf PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

    # STDX freestanding: bypass {fmt} dependency in cpp-std-extensions.
    target_compile_definitions(novavisor.elf PRIVATE STDX_FREESTANDING)

    target_link_options(novavisor.elf PRIVATE
        -T ${HAL_ARMV8_LINKER_SCRIPT}
        -nostartfiles
        -nostdlib
        -Wl,--gc-sections
    )

    # Raw flat binary required by some QEMU boot modes.
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

    # Stage 2 descriptor encoding (components/core_mmu/include/stage2_descriptor.hpp).
    # Header-only, constexpr — depends on <cstdint> only.
    add_executable(stage2_descriptor_test tests/host/stage2_descriptor_test.cpp)
    target_include_directories(stage2_descriptor_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_link_libraries(stage2_descriptor_test PRIVATE GTest::gtest_main)
    add_test(NAME stage2_descriptor_test COMMAND stage2_descriptor_test)

    # Stage 2 identity-map builder (components/core_mmu/include/stage2_builder.hpp).
    # Header-only, depends only on stage2_descriptor.hpp + std arrays.
    add_executable(stage2_builder_test tests/host/stage2_builder_test.cpp)
    target_include_directories(stage2_builder_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
    target_link_libraries(stage2_builder_test PRIVATE GTest::gtest_main)
    add_test(NAME stage2_builder_test COMMAND stage2_builder_test)

endif() # CMAKE_CROSSCOMPILING
