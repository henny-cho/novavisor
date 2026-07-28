#ifndef NOVA_N1SDP_BOARD_LAYOUT_H
#define NOVA_N1SDP_BOARD_LAYOUT_H

// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

/* TF-A loads BL33 at 0xE0000000. Guest state stays in the lower
 * non-secure DRAM bank and does not overlap the EL2 image. */
/* Board identity for the boot report. */
#define NOVA_BOARD_NAME "n1sdp"

#define NOVA_BOARD_RAM_BASE      0xE0000000
#define NOVA_BOARD_RAM_SIZE      0x08000000
#define NOVA_BOARD_PHYS_RAM_BASE 0x80000000
#define NOVA_BOARD_PHYS_RAM_SIZE 0x7F000000
#define NOVA_BOARD_GUEST_PA_BASE 0x80000000
#define NOVA_BOARD_GUEST_PA_SIZE 0x20000000 /* windows end at the IVC page */
#define NOVA_BOARD_IVC_SHM_PA    0xA0000000
#define NOVA_BOARD_PRISTINE_PA   0xA0100000
#define NOVA_BOARD_PRISTINE_SIZE 0x3FF00000 /* below the EL2 image at RAM_BASE */

#define NOVA_BOARD_GUEST_CPU_COMPATIBLE "arm,neoverse-n1"

#define NOVA_BOARD_SMP_CPUS       4
#define NOVA_BOARD_BOOT_MPIDR     0x0
#define NOVA_BOARD_EL2_STACK_SIZE 0x4000

/* Non-secure SoC PL011 exposed as serial0 by the platform DT. */
#define NOVA_BOARD_UART0_BASE  0x2A400000
#define NOVA_BOARD_UART0_INTID 95

/* GIC-600, single-chip redistributor region. */
#define NOVA_BOARD_GICD_BASE 0x30000000
#define NOVA_BOARD_GICR_BASE 0x300C0000

/* PCIe MMU-600 TCU. DTS SPI values 235..237 map to INTIDs 267..269. */
#define NOVA_BOARD_SMMU_BASE        0x4F400000
#define NOVA_BOARD_SMMU_SIZE        0x00040000
#define NOVA_BOARD_SMMU_EVENT_INTID 267
#define NOVA_BOARD_SMMU_CMD_INTID   268
#define NOVA_BOARD_SMMU_ERROR_INTID 269

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_N1SDP_BOARD_LAYOUT_H */
