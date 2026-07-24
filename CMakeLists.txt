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

# Opportunistic ccache: transparently reused across all build trees and CI
# runs; a plain compiler invocation when ccache is absent.
find_program(CCACHE_PROGRAM ccache)
if(CCACHE_PROGRAM)
    set(CMAKE_C_COMPILER_LAUNCHER ${CCACHE_PROGRAM})
    set(CMAKE_CXX_COMPILER_LAUNCHER ${CCACHE_PROGRAM})
endif()

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
    # Debug builds get libstdc++ hardening: std::array/string_view subscripts
    # bounds-check at runtime and land in the freestanding
    # __glibcxx_assert_fail panic sink (components/nova_panic). Release stays
    # unchecked — zero-cost in the shipped image.
    target_compile_definitions(nova_warnings INTERFACE
        $<$<AND:$<COMPILE_LANGUAGE:CXX>,$<CONFIG:Debug>>:_GLIBCXX_ASSERTIONS>
    )
    # STDX freestanding: bypass the {fmt} dependency in cpp-std-extensions.
    # On the shared interface target (not just the ELF) so every cross TU
    # compiles the stdx headers the same way.
    target_compile_definitions(nova_warnings INTERFACE
        $<$<COMPILE_LANGUAGE:CXX>:STDX_FREESTANDING>
    )
    # EL2 must never touch FP/SIMD registers: CPTR_EL2.TFP would self-trap
    # the hypervisor, and any use clobbers lazily-switched guest FP state.
    # Directory-wide (not per-target) so every cross TU is covered; .S files
    # are exempt — the FP bank save/restore itself is hand-written asm.
    # Enforced post-link by scripts/check_fp_free.sh on novavisor.elf.
    add_compile_options($<$<COMPILE_LANGUAGE:C,CXX>:-mgeneral-regs-only>)
endif()

# Clang-Tidy retarget args for `scripts/task.sh lint` (cross builds only).
include(cmake/clang_tidy.cmake)

# Target-definition helpers (nova_add_component, nova_add_host_test).
include(cmake/nova_helpers.cmake)
include(cmake/nova_platform_contract.cmake)

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

    set(NOVA_ARCH "aarch64" CACHE STRING "Architecture HAL")
    set(NOVA_BOARD "qemu_virt" CACHE STRING "Board HAL")
    set(NOVA_PROJECT "qemu_virt_arm64" CACHE STRING "Composition project")
    foreach(selection IN ITEMS NOVA_ARCH NOVA_BOARD NOVA_PROJECT)
        if(NOT "${${selection}}" MATCHES "^[A-Za-z0-9_]+$")
            message(FATAL_ERROR "${selection} must be an identifier: '${${selection}}'")
        endif()
    endforeach()

    set(NOVA_ARCH_DIR "${CMAKE_SOURCE_DIR}/src/hal/arch/${NOVA_ARCH}")
    set(NOVA_BOARD_DIR "${CMAKE_SOURCE_DIR}/src/hal/board/${NOVA_BOARD}")
    set(NOVA_PROJECT_DIR "${CMAKE_SOURCE_DIR}/src/projects/${NOVA_PROJECT}")
    set(NOVA_BOARD_INCLUDE_DIR "${NOVA_BOARD_DIR}/include")
    nova_validate_selection_paths()
    include("${NOVA_BOARD_DIR}/board.cmake")
    include("${NOVA_PROJECT_DIR}/project.cmake")
    nova_validate_platform_manifest()
    message(STATUS "Nova platform: arch=${NOVA_ARCH}, board=${NOVA_BOARD}, project=${NOVA_PROJECT}")

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

    # Only the selected architecture and board enter the build graph.
    add_subdirectory("${NOVA_BOARD_DIR}" "${CMAKE_BINARY_DIR}/platform/board")
    add_subdirectory("${NOVA_ARCH_DIR}" "${CMAKE_BINARY_DIR}/platform/arch")
    nova_validate_platform_targets()
    add_library(nova_platform INTERFACE)
    target_link_libraries(nova_platform INTERFACE nova_board)

    # Only components selected by the active composition project are built.
    foreach(component IN LISTS NOVA_COMPONENTS)
        if(NOT EXISTS "${CMAKE_SOURCE_DIR}/src/components/${component}/CMakeLists.txt")
            message(FATAL_ERROR "Project '${NOVA_PROJECT}' selected unknown component '${component}'")
        endif()
        add_subdirectory(src/components/${component})
    endforeach()

    # Composition root: nexus + guest table + the novavisor.elf target.
    include(cmake/nova_guest_project.cmake)
    add_subdirectory("${NOVA_PROJECT_DIR}" "${CMAKE_BINARY_DIR}/platform/project")

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

    # Foundation-tree tests (tests/host/).
    nova_add_host_test(esr_parse_test)  # nova/arch/esr.hpp + trap_context.hpp
    nova_add_host_test(data_abort_test) # nova/arch/data_abort.hpp MMIO decode
    nova_add_host_test(fmt_test)        # nova/fmt.hpp integer formatters
    nova_add_host_test(spin_lock_test)     # nova/sync.hpp mutual exclusion
    nova_add_host_test(restore_model_test) # sparse pristine restore
    nova_add_host_test(dma_policy_test)    # Stream ID ownership and DMA windows
    nova_add_host_test(gic_spi_test)       # arbitrary physical SPI banks
    nova_add_host_test(pci_test)           # requester IDs and ECAM layout

    # Component tests (src/components/<name>/test/).
    nova_add_component_test(core_mmu stage2_descriptor_test) # Stage 2 descriptor encoding
    nova_add_component_test(core_mmu stage2_builder_test)    # Stage 2 identity-map builder
    nova_add_component_test(vgic vgic_model_test)            # GICD/GICR register model
    nova_add_component_test(vgic vgic_delivery_test)         # pending → LR multiplexing
    nova_add_component_test(soft_timer timer_queue_test)     # deadline-slot queue
    nova_add_component_test(core_vcpu sched_model_test)      # scheduler pick/predicates
    nova_add_component_test(core_vcpu fp_model_test)         # lazy FP ownership
    nova_add_component_test(core_vcpu lifecycle_model_test)  # micro-reboot budget
    nova_add_component_test(psci psci_model_test)            # SMCCC range dispatch
    nova_add_component_test(watchdog watchdog_model_test)    # owner generation + deadline
    nova_add_component_test(dma_device device_lifecycle_model_test) # per-device lifecycle registry
    nova_add_component_test(ivc ring_test)                   # SPSC ring protocol (threads)
    nova_add_component_test(vuart vuart_model_test)          # PL011 register model
    nova_add_component_test(dtb_parser fdt_model_test)       # FDT walker + guest extraction
    nova_add_component_test(smmu ste_model_test)             # Stage-2-only STE encoding
    nova_add_component_test(smmu queue_model_test)           # CMDQ/EVTQ phase transitions
    nova_add_component_test(smmu fault_model_test)           # SID/address fault records
    nova_add_component_test(smmu runtime_model_test)         # runtime capabilities and layout
    nova_add_component_test(smmu command_model_test)         # command entry encoding
    nova_add_component_test(smmu domain_model_test)          # guest DMA context lifecycle
    nova_add_component_test(smmu dma_table_model_test)       # DMA-only Stage-2 mappings
    target_include_directories(dma_table_model_test PRIVATE
        ${CMAKE_SOURCE_DIR}/src/components/core_mmu/include)

endif() # CMAKE_CROSSCOMPILING
