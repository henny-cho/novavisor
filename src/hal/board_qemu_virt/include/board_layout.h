/* hal/board_qemu_virt/include/board_layout.h
 *
 * Board RAM window for the hypervisor image — the single source shared
 * by the linker-script template (hal/armv8_aarch64/linker.ld.S,
 * preprocessed with cc -E) and any future C/C++ consumer. Porting to a
 * new board changes these values (plus the peripheral bases in
 * board.hpp), nothing else.
 *
 * Plain #defines only: this header must survive the C preprocessor in
 * linker-script context.
 */

#ifndef NOVA_BOARD_LAYOUT_H
#define NOVA_BOARD_LAYOUT_H

/* Macros are the point here (linker-script consumer) — the usual
 * constexpr guidance does not apply. */
// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

/* QEMU virt: RAM starts at 1 GiB. The hypervisor image + stack must fit
 * in this budget; guest windows (nova/guest_layout.h) live above it. */
#define NOVA_BOARD_RAM_BASE 0x40000000
#define NOVA_BOARD_RAM_SIZE 0x08000000 /* 128 MiB */

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_BOARD_LAYOUT_H */
