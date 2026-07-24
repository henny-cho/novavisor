/* hal/board/qemu_virt/include/board_layout.h
 *
 * The QEMU virt memory map — the single source shared by the
 * linker-script template (hal/arch/aarch64/linker.ld.S, preprocessed
 * with cc -E) and the C++ view in board.hpp. Porting to a new board
 * changes these values, nothing else.
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
 * in this budget; guest windows (nova/abi/guest_layout.h) live above it. */
#define NOVA_BOARD_RAM_BASE      0x40000000
#define NOVA_BOARD_RAM_SIZE      0x08000000 /* 128 MiB */
#define NOVA_BOARD_PHYS_RAM_SIZE 0x40000000 /* QEMU -m 1024 */

/* Guest CPU node exposed by the generated DT. */
#define NOVA_BOARD_GUEST_CPU_COMPATIBLE "arm,cortex-a57"

/* Physical CPU count (QEMU -smp must match) and the EL2 stack carved
 * out per core by the linker script; core i's SP starts at
 * __stack_top - i * NOVA_BOARD_EL2_STACK_SIZE (boot.S). */
#define NOVA_BOARD_SMP_CPUS       2
#define NOVA_BOARD_EL2_STACK_SIZE 0x4000 /* 16 KiB per core */

/* Peripheral bases. */
#define NOVA_BOARD_UART0_BASE  0x09000000
#define NOVA_BOARD_UART0_INTID 33

/* GICv3 (requires -machine virt,gic-version=3). */
#define NOVA_BOARD_GICD_BASE 0x08000000 /* distributor */
#define NOVA_BOARD_GICR_BASE 0x080A0000 /* redistributor frame, CPU 0 */

/* SMMUv3 (requires -machine virt,iommu=smmuv3). */
#define NOVA_BOARD_SMMU_BASE        0x09050000
#define NOVA_BOARD_SMMU_SIZE        0x00020000
#define NOVA_BOARD_SMMU_EVENT_INTID 106
#define NOVA_BOARD_SMMU_CMD_INTID   108
#define NOVA_BOARD_SMMU_ERROR_INTID 109

/* Low PCIe windows (requires -machine virt,highmem-ecam=off). */
#define NOVA_BOARD_PCIE_MMIO_BASE 0x10000000
#define NOVA_BOARD_PCIE_ECAM_BASE 0x3F000000

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_BOARD_LAYOUT_H */
