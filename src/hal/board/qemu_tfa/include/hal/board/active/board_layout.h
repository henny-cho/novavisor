/* hal/board/active/board_layout.h (qemu_tfa)
 *
 * QEMU virt booted through a real TF-A chain (BL1→BL2→BL31→BL33)
 * instead of -kernel. Same machine as qemu_virt; only the memory map
 * differs, for two firmware-imposed reasons:
 *
 *   - TF-A's qemu platform loads BL33 from the FIP at 0x60000000
 *     (NS_IMAGE_OFFSET), so the image window moves there.
 *   - QEMU's -bios mode places the generated DTB at the base of RAM
 *     (0x40000000) for the firmware to consume; nothing may be loaded
 *     over it.
 *
 * Guest windows, the IVC page and the pristine area pack below the
 * image instead of above it.
 */

#ifndef NOVA_BOARD_LAYOUT_H
#define NOVA_BOARD_LAYOUT_H

/* Macros are the point here (linker-script consumer) — the usual
 * constexpr guidance does not apply. */
// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

/* Board identity for the boot report. */
#define NOVA_BOARD_NAME "qemu_tfa"

#define NOVA_BOARD_RAM_BASE      0x60000000 /* TF-A qemu NS_IMAGE_OFFSET */
#define NOVA_BOARD_RAM_SIZE      0x08000000
#define NOVA_BOARD_PHYS_RAM_BASE 0x40000000
#define NOVA_BOARD_PHYS_RAM_SIZE 0x40000000 /* QEMU -m 1024 */
#define NOVA_BOARD_GUEST_PA_BASE 0x50000000
#define NOVA_BOARD_GUEST_PA_SIZE 0x10000000 /* windows end at the EL2 image */
#define NOVA_BOARD_IVC_SHM_PA    0x48000000 /* above the firmware DTB region */
/* Trace rings. Sized by the host stall the T layer must survive: 133k
 * records/s/core at peak, times a declared 1 s horizon. Two cores
 * divide it, so each gets 2^18 records — 1.97 s of that peak.
 * EL2-mapped already, never in a guest's Stage 2. */
#define NOVA_BOARD_TRACE_PA      0x48001000
#define NOVA_BOARD_TRACE_SIZE    0x01010000 /* 16 MiB + 64 KiB */
#define NOVA_BOARD_PRISTINE_PA   0x49100000
#define NOVA_BOARD_PRISTINE_SIZE 0x06F00000 /* up to the guest windows */

/* Guest CPU node exposed by the generated DT. */
#define NOVA_BOARD_GUEST_CPU_COMPATIBLE "arm,cortex-a57"

#define NOVA_BOARD_SMP_CPUS       2
#define NOVA_BOARD_BOOT_MPIDR     0x0
#define NOVA_BOARD_EL2_STACK_SIZE 0x4000 /* 16 KiB per core */

/* Peripheral bases — identical to qemu_virt (same machine). */
#define NOVA_BOARD_UART0_BASE  0x09000000
#define NOVA_BOARD_UART0_INTID 33

#define NOVA_BOARD_GICD_BASE 0x08000000
#define NOVA_BOARD_GICR_BASE 0x080A0000

#define NOVA_BOARD_SMMU_BASE        0x09050000
#define NOVA_BOARD_SMMU_SIZE        0x00020000
#define NOVA_BOARD_SMMU_EVENT_INTID 106
#define NOVA_BOARD_SMMU_CMD_INTID   108
#define NOVA_BOARD_SMMU_ERROR_INTID 109

#define NOVA_BOARD_PCIE_MMIO_BASE 0x10000000
#define NOVA_BOARD_PCIE_ECAM_BASE 0x3F000000

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_BOARD_LAYOUT_H */
