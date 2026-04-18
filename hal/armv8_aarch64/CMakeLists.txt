# armv8_aarch64 — low-level boot + vector table for ARMv8-A EL2.
# OBJECT library so the asm sources land in the final ELF with correct LMA.
add_library(hal_armv8_aarch64 OBJECT
    boot.S
    vec.S
    mmu.S
    vcpu_enter.S
)
target_include_directories(hal_armv8_aarch64 PUBLIC ${CMAKE_SOURCE_DIR})

# Expose the linker script path to consumers.
set(HAL_ARMV8_LINKER_SCRIPT "${CMAKE_CURRENT_SOURCE_DIR}/linker.ld" CACHE INTERNAL "")
