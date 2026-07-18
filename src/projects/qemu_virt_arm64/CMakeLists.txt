# qemu_virt_arm64 — the composition root: picks the board/arch HAL,
# composes the components (nexus.hpp), defines the guest table, and
# links the final ELF. Adding a project/board means adding a sibling
# directory, not touching the root build.

add_executable(novavisor.elf
    main.cpp
    # Defines nova::guest_table() — the project-side injection point
    # consumed by core_mmu/core_vcpu via nova/abi/guest.hpp.
    guest_config.cpp
)

# Outputs stay at the build root — scripts (task.sh run/debug,
# demo_runner) address build/novavisor.{elf,bin}.
set_target_properties(novavisor.elf PROPERTIES RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})

target_include_directories(novavisor.elf PRIVATE
    ${CMAKE_SOURCE_DIR}/src
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(novavisor.elf PRIVATE
    hal_arch_aarch64
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
    -T ${HAL_ARCH_LINKER_SCRIPT}
    -nostartfiles
    -nostdlib
    -Wl,--gc-sections
)
# The script is generated (linker.ld.S template + board_layout.h);
# make sure it exists before linking and relink when it changes.
add_dependencies(novavisor.elf hal_arch_linker_script)
set_target_properties(novavisor.elf PROPERTIES LINK_DEPENDS ${HAL_ARCH_LINKER_SCRIPT})

# Raw flat binary required by some QEMU boot modes.
add_custom_command(TARGET novavisor.elf POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:novavisor.elf> ${CMAKE_BINARY_DIR}/novavisor.bin
    BYPRODUCTS ${CMAKE_BINARY_DIR}/novavisor.bin
    COMMENT "Generating flat binary novavisor.bin"
)
