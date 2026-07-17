cmake_minimum_required(VERSION 3.25)
project(NovaVisor LANGUAGES C CXX ASM)

# ==============================================================================
# Global C++ Configuration
# ==============================================================================
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Enable exporting of compile_commands.json for clang-tidy/clangd via VSCode
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Warning and codegen flags for NovaVisor's own targets. An INTERFACE target
# instead of directory-wide add_compile_options so fetched third-party code
# never inherits -Werror — a dep warning must not break our build on
# toolchain upgrades.
add_library(nova_warnings INTERFACE)
target_compile_options(nova_warnings INTERFACE
    $<$<COMPILE_LANGUAGE:CXX>:-Wall;-Wextra;-Wpedantic;-Werror>
)
if(CMAKE_CROSSCOMPILING)
    # Zero-cost abstraction rules: No RTTI, No Exceptions (bare-metal only)
    target_compile_options(nova_warnings INTERFACE
        $<$<COMPILE_LANGUAGE:CXX>:-fno-rtti;-fno-exceptions>
    )
endif()

# Clang-Tidy retarget args for `scripts/task.sh lint` (cross builds only).
include(cmake/clang_tidy.cmake)

# Target-definition helpers (nova_add_component, nova_add_host_test).
include(cmake/nova_helpers.cmake)

# CMake Package Manager (CPM) — v0.42.1. Must be included before any CPM
# dependency so CIB's bundled get_cpm.cmake (pinned at v0.40.2) finds CPM
# already loaded and skips its own download. All CPMAddPackage sources land
# in CPM_SOURCE_CACHE (external/cache, exported by scripts/task.sh) and are
# shared across build trees.
include(cmake/get_cpm.cmake)
include(cmake/cib_cpm_pin.cmake)

if(CMAKE_CROSSCOMPILING)
    # ===========================================================================
    # Bare-metal ELF (aarch64-none-elf cross build)
    # ===========================================================================

    # Silence CPM version-mismatch warnings from CIB's transitive deps.
    nova_pin_cib_transitive_packages()

    # 1. Intel compile-time-init-build (cib)
    # Pinned to the commit before cib main adopted add_versioned_package(URI ...)
    # keyword syntax, which the bundled cicd-repo-infrastructure@3e2bef0 does not
    # yet forward to CPM — causing CPM NAME inference to fail for boost_mp11.
    message(STATUS "Fetching Intel cib library...")
    CPMAddPackage(
        NAME cib
        GITHUB_REPOSITORY intel/compile-time-init-build
        GIT_TAG 488d892
    )

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
    CPMAddPackage(
        NAME etl
        GITHUB_REPOSITORY ETLCPP/etl
        GIT_TAG 3e4d41ca57de475f4b769320308e80a103c8f5d6
    )

    # HAL (each owns its own CMakeLists.txt).
    add_subdirectory(hal/armv8_aarch64)
    add_subdirectory(hal/board_qemu_virt)

    # Components — one add_subdirectory and one link entry per name.
    # Ordering only matters for inter-component DEPS (nova_panic before
    # trap_handler). idle is built but not composed: nexus.hpp swapped it out
    # for EL1 guest execution in Phase 5; Phase 11 may reintroduce it.
    set(NOVA_COMPONENTS
        nova_panic
        trap_handler
        hal_init
        boot_msg
        idle
        core_mmu
        core_vcpu
        demo_hvc
    )
    foreach(component IN LISTS NOVA_COMPONENTS)
        add_subdirectory(components/${component})
    endforeach()

    add_executable(novavisor.elf projects/qemu_virt_arm64/main.cpp)

    target_include_directories(novavisor.elf PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

    # STDX freestanding: bypass {fmt} dependency in cpp-std-extensions.
    target_compile_definitions(novavisor.elf PRIVATE STDX_FREESTANDING)

    target_link_libraries(novavisor.elf PRIVATE
        hal_armv8_aarch64
        hal_board_qemu_virt
        ${NOVA_COMPONENTS}
        # Frameworks
        cib etl
        nova_warnings
        # Bare-metal C runtime (strlen, memcpy, etc.) from newlib.
        # --start-group/--end-group resolves libgcc↔libc circular deps —
        # otherwise strlen references from char_traits::length only resolve
        # at -O2 where the call is inlined. No dynamic allocation introduced.
        -Wl,--start-group -lgcc -lc -Wl,--end-group
    )

    target_link_options(novavisor.elf PRIVATE
        -T ${HAL_ARMV8_LINKER_SCRIPT}
        -nostartfiles
        -nostdlib
        -Wl,--gc-sections
    )

    # Raw flat binary required by some QEMU boot modes.
    add_custom_command(TARGET novavisor.elf POST_BUILD
        COMMAND ${CMAKE_OBJCOPY} -O binary novavisor.elf novavisor.bin
        BYPRODUCTS ${CMAKE_CURRENT_BINARY_DIR}/novavisor.bin
        COMMENT "Generating flat binary novavisor.bin"
    )

else()
    # ===========================================================================
    # Host GTest suite (native build) — header-only utilities that depend
    # only on <cstdint>, so none of the bare-metal deps (cib/etl) are fetched.
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

    nova_add_host_test(esr_parse_test)         # nova/esr.hpp + trap_context.hpp
    nova_add_host_test(stage2_descriptor_test) # core_mmu Stage 2 descriptor encoding
    nova_add_host_test(stage2_builder_test)    # core_mmu Stage 2 identity-map builder

endif() # CMAKE_CROSSCOMPILING
