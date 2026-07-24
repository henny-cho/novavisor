# Composition root for the selected AArch64 virt machine.

# Guest configuration pipeline: active_config.yml (seeded from
# configs/default.yml at configure time, switched in place by
# `task.sh build --config` — no reconfigure) -> per-guest DTB blobs +
# the .incbin assembly that embeds them.
set(GUEST_CONFIG ${CMAKE_BINARY_DIR}/active_config.yml)
if(NOT EXISTS ${GUEST_CONFIG})
    file(COPY_FILE ${CMAKE_SOURCE_DIR}/configs/default.yml ${GUEST_CONFIG})
endif()
set(GUEST_DTB_DIR ${CMAKE_BINARY_DIR}/guest_dtb)
add_custom_command(
    OUTPUT ${GUEST_DTB_DIR}/guest_dtbs.S
           ${GUEST_DTB_DIR}/device_policy.hpp
    COMMAND python3 ${CMAKE_SOURCE_DIR}/tools/yml2dtb/yml2dtb.py
            ${GUEST_CONFIG}
            -o ${GUEST_DTB_DIR}
            --board-layout ${NOVA_BOARD_INCLUDE_DIR}/board_layout.h
            --inventory ${NOVA_BOARD_DIR}/device_inventory.yml
    DEPENDS ${GUEST_CONFIG}
            ${CMAKE_SOURCE_DIR}/tools/yml2dtb/yml2dtb.py
            ${CMAKE_SOURCE_DIR}/src/nova/abi/guest_layout.h
            ${CMAKE_SOURCE_DIR}/src/nova/arch/gicv3_regs.h
            ${NOVA_BOARD_INCLUDE_DIR}/board_layout.h
            ${NOVA_BOARD_DIR}/device_inventory.yml
    COMMENT "yml2dtb: generating guest DTBs from ${GUEST_CONFIG}"
)

add_executable(novavisor.elf
    main.cpp
    # Defines nova::guest_table() — the project-side injection point
    # consumed by core_mmu/core_vcpu via nova/abi/guest.hpp. Populated
    # at boot from the embedded guest DTBs.
    guest_config.cpp
    ${GUEST_DTB_DIR}/guest_dtbs.S
    ${GUEST_DTB_DIR}/device_policy.hpp
)

# Outputs stay at the build root — scripts (task.sh run/debug,
# demo_runner) address build/novavisor.{elf,bin}.
set_target_properties(novavisor.elf PROPERTIES RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})

target_include_directories(novavisor.elf PRIVATE
    ${CMAKE_SOURCE_DIR}/src
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${GUEST_DTB_DIR}
)

target_link_libraries(novavisor.elf PRIVATE
    nova_arch
    nova_platform
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
    -T ${NOVA_LINKER_SCRIPT}
    -nostartfiles
    -nostdlib
    -Wl,--gc-sections
)
# The script is generated (linker.ld.S template + board_layout.h);
# make sure it exists before linking and relink when it changes.
add_dependencies(novavisor.elf nova_arch_linker_script)
set_target_properties(novavisor.elf PROPERTIES LINK_DEPENDS ${NOVA_LINKER_SCRIPT})

# EL2 must be FP/SIMD-free outside the FP bank save/restore asm
# (CPTR_EL2.TFP + lazy guest FP switching). -mgeneral-regs-only covers
# compiler output; this catches prebuilt libc routines the flag cannot.
add_custom_command(TARGET novavisor.elf POST_BUILD
    COMMAND ${CMAKE_SOURCE_DIR}/scripts/check_fp_free.sh ${CMAKE_OBJDUMP}
            $<TARGET_FILE:novavisor.elf>
    COMMENT "Checking novavisor.elf is FP/SIMD-free"
)

# Raw flat binary required by some QEMU boot modes.
add_custom_command(TARGET novavisor.elf POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:novavisor.elf> ${CMAKE_BINARY_DIR}/novavisor.bin
    BYPRODUCTS ${CMAKE_BINARY_DIR}/novavisor.bin
    COMMENT "Generating flat binary novavisor.bin"
)
