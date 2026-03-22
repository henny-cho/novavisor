# cmake/pigweed_setup.cmake
#
# Integrates Google Pigweed modules into the NovaVisor bare-metal build.
# Only active for cross-compiled (AArch64) targets; host GTest builds skip
# this file entirely via the early-return guard.
#
# Module tiers:
#   pw_tier1 — core library aliases used in novavisor/types.hpp
#   pw_tier2 — optional modules, linked when a phase actually needs them

# Cross-compile only — host GTest build is unaffected
if(NOT CMAKE_CROSSCOMPILING)
    return()
endif()

# ==============================================================================
# Step 1: Fetch Pigweed and run its top-level CMakeLists.txt
#
# FetchContent_MakeAvailable runs Pigweed's own CMakeLists.txt which:
#   - Includes pw_build/pigweed.cmake (defines pw_set_backend etc.)
#   - Adds ALL Pigweed modules with EXCLUDE_FROM_ALL
#
# The EXCLUDE_FROM_ALL flag means no module is compiled unless explicitly linked.
# Running the top-level CMakeLists.txt also ensures every module's targets are
# defined, so Pigweed's deferred dependency validation passes without errors.
#
# Note: FetchContent_MakeAvailable sets PW_ROOT automatically via pigweed.cmake.
# Note: pw_unit_test_ENABLE_PW_ADD_TEST is set OFF to suppress test-target
#       generation inside module CMakeLists files (prevents missing dep errors
#       from pw_unit_test which we do not add to the build).
#
# CRITICAL — pw_assert backend must be pre-set BEFORE FetchContent_MakeAvailable:
#   pw_add_facade() reads ${pw_assert.assert_BACKEND} at the time it runs and
#   bakes the backend name into the facade target as a PUBLIC_DEP. If the cache
#   variable is empty at that point, pw_add_facade creates a NO_BACKEND_SET
#   error target — calling pw_set_backend() afterwards cannot change it.
#   Setting the CACHE variables here (before MakeAvailable) ensures pw_add_facade
#   sees "nova_assert_backend" and links correctly. The nova_assert_backend target
#   is created after MakeAvailable; Pigweed's deferred dep check fires at the
#   end of configure by which point the target exists.
# ==============================================================================
set(pw_unit_test_ENABLE_PW_ADD_TEST OFF CACHE BOOL "" FORCE)
set(pw_assert.assert_BACKEND "nova_assert_backend" CACHE STRING "" FORCE)
set(pw_assert.check_BACKEND "nova_assert_backend" CACHE STRING "" FORCE)

include(FetchContent)
FetchContent_Declare(
    pigweed
    GIT_REPOSITORY https://pigweed.googlesource.com/pigweed/pigweed
    GIT_TAG main
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(pigweed)

# ==============================================================================
# Step 2: Build the nova_assert_backend static library
#
# Routes PW_ASSERT / PW_CHECK / PW_CRASH to the same UART + WFI halt path as
# CIB stdx::panic (nova_panic). Avoids pw_assert_basic's large ROM/RAM overhead.
#
# Created after FetchContent_MakeAvailable but before end-of-configure, so
# Pigweed's deferred dependency check finds the target in time.
# ==============================================================================
add_library(nova_assert_backend STATIC
    ${CMAKE_SOURCE_DIR}/components/nova_panic/src/pw_assert_backend.cc
)
target_include_directories(
    nova_assert_backend
    PUBLIC ${CMAKE_SOURCE_DIR}/components/nova_panic/include # pw_assert_backend/*.h
           ${CMAKE_SOURCE_DIR} # project root (hal/board_qemu_virt/include/uart.hpp)
)

# ==============================================================================
# Step 3: Aggregate interface targets
#
# Consumers link pw_tier1 / pw_tier2 — not individual pw_ targets. Add modules
# here when a new phase requires them; novavisor.elf's target_link_libraries
# line does not need to change.
#
# Future: pw_sync_baremetal (Phase 10 IVC) — add pw_set_backend for pw_sync
# backends and link pw_sync_baremetal to pw_tier2.
# ==============================================================================
add_library(pw_tier1 INTERFACE)
target_link_libraries(
    pw_tier1
    INTERFACE
        pw_assert # PW_CHECK / PW_ASSERT — boundary and invariant checks
        pw_span # novavisor::Span<T>, novavisor::ConstSpan<T>
        pw_status # novavisor::Status
        pw_result # novavisor::Result<T>
        pw_string # novavisor::InlineString<N>, novavisor::StringBuffer<N>
        pw_bytes # pw::bytes::ReadInOrder / WriteInOrder (DTB parsing)
)

add_library(pw_tier2 INTERFACE)
target_link_libraries(
    pw_tier2
    INTERFACE
        pw_checksum # CRC32 (Phase 12: DTB integrity)
        pw_function # pw::Function<> (Phase 8: console_mux callbacks)
)
