# hal/arch/aarch64 — low-level boot + vector table for ARMv8-A EL2.
# OBJECT library so the asm sources land in the final ELF with correct LMA.
add_library(hal_arch_aarch64 OBJECT
    boot.S
    vec.S
    mmu.S
    vcpu_enter.S
    fp.S
    mem.cpp
)
target_include_directories(hal_arch_aarch64 PUBLIC ${CMAKE_SOURCE_DIR}/src)

# mem.cpp implements the mem* libcalls themselves: -fno-builtin keeps
# GCC from recognizing the loops and emitting recursive libcalls.
set_source_files_properties(mem.cpp PROPERTIES COMPILE_OPTIONS "-fno-builtin")

# The linker script is preprocessed from a template so the RAM window
# comes from the board's board_layout.h (same lds.S pattern as
# demo/CMakeLists.txt). -P strips linemarkers; -x c treats the template
# as C for the preprocessor only.
set(HAL_ARCH_LINKER_SCRIPT "${CMAKE_CURRENT_BINARY_DIR}/linker.ld" CACHE INTERNAL "")
add_custom_command(
    OUTPUT ${HAL_ARCH_LINKER_SCRIPT}
    COMMAND ${CMAKE_C_COMPILER} -E -P -x c -I${CMAKE_SOURCE_DIR}/src
            ${CMAKE_CURRENT_SOURCE_DIR}/linker.ld.S -o ${HAL_ARCH_LINKER_SCRIPT}
    DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/linker.ld.S
            ${CMAKE_SOURCE_DIR}/src/hal/board/qemu_virt/include/board_layout.h
    COMMENT "Preprocessing hypervisor linker script"
)
add_custom_target(hal_arch_linker_script DEPENDS ${HAL_ARCH_LINKER_SCRIPT})
