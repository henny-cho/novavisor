# Low-level boot + vector table for ARMv8-A EL2. The selected
# architecture exports the canonical target consumed by the project.
add_library(nova_arch OBJECT
    boot.S
    vec.S
    mmu.S
    vcpu_enter.S
    fp.S
    mem.cpp
)
target_include_directories(nova_arch PUBLIC
    ${CMAKE_SOURCE_DIR}/src
    ${NOVA_BOARD_INCLUDE_DIR})

# mem.cpp implements the mem* libcalls themselves: -fno-builtin keeps
# GCC from recognizing the loops and emitting recursive libcalls.
set_source_files_properties(mem.cpp PROPERTIES COMPILE_OPTIONS "-fno-builtin")

# The linker script is preprocessed from a template so the RAM window
# comes from the board's board_layout.h (same lds.S pattern as
# demo/CMakeLists.txt). -P strips linemarkers; -x c treats the template
# as C for the preprocessor only.
set(NOVA_LINKER_SCRIPT "${CMAKE_CURRENT_BINARY_DIR}/linker.ld")
add_custom_command(
    OUTPUT ${NOVA_LINKER_SCRIPT}
    COMMAND ${CMAKE_C_COMPILER} -E -P -x c
            -I${NOVA_BOARD_INCLUDE_DIR}
            -I${CMAKE_SOURCE_DIR}/src
            ${CMAKE_CURRENT_SOURCE_DIR}/linker.ld.S -o ${NOVA_LINKER_SCRIPT}
    DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/linker.ld.S
            ${NOVA_BOARD_INCLUDE_DIR}/hal/board/active/board_layout.h
            ${NOVA_BOARD_INCLUDE_DIR}/board_layout.h
    COMMENT "Preprocessing hypervisor linker script"
)
add_custom_target(nova_arch_linker_script DEPENDS ${NOVA_LINKER_SCRIPT})
set(NOVA_LINKER_SCRIPT "${NOVA_LINKER_SCRIPT}" PARENT_SCOPE)
