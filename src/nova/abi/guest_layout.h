/* nova/abi/guest_layout.h
 *
 * Guest memory window shared by the selected project configuration and
 * guest linker script for Stage 2 mapping, entry, stack, and DTB placement.
 *
 * Plain #defines only: this header must survive the assembler, the
 * linker-script preprocessor, and the C/C++ compilers alike.
 */

#ifndef NOVA_GUEST_LAYOUT_H
#define NOVA_GUEST_LAYOUT_H

/* Macros are the point here (assembler/linker-script consumers) — the
 * usual constexpr guidance does not apply. */
// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

/* Every guest sees the same IPA window (and links against it); the
 * backing PA differs per guest by slot. */
#define NOVA_GUEST_IPA_BASE 0x50000000
#define NOVA_GUEST_IPA_SIZE 0x00100000 /* 1 MiB */

/* Guest PA windows are packed: guest i starts where guest i-1 ended,
 * rounded up to this alignment (keeps every window Block-mappable and
 * slot 0 identity with the IPA window). A demo manifest's
 * guests[].load_addr must equal the packed PA tools/yml2dtb computes. */
#define NOVA_GUEST_PA_ALIGN 0x00200000 /* 2 MiB */

/* IVC shared page: one 4 KiB page mapped RW (XN) into every VM.
 * Its physical location is selected by the active board. */
#define NOVA_IVC_SHM_IPA  0x60000000
#define NOVA_IVC_SHM_SIZE 0x00001000 /* 4 KiB */

/* Emulated GICv3 frames: left unmapped in Stage 2 so every access traps
 * into the vGIC. Register offsets come from gicv3_regs.h. */
#define NOVA_GICD_IPA_BASE 0x08000000
#define NOVA_GICR_IPA_BASE 0x080A0000

/* Emulated PL011 uses the same unmapped-frame technique. */
#define NOVA_VUART_IPA_BASE 0x09000000
#define NOVA_VUART_IPA_SIZE 0x00001000
#define NOVA_VUART_SPI      33

/* Direct-assignment contract for the educational PCI device. The BAR
 * is mapped only into its owner VM; its level SPI is delivered through
 * the virtual GIC and rearmed after the guest's EOI. */
#define NOVA_EDU_BAR0_IPA  0x10000000
#define NOVA_EDU_BAR0_SIZE 0x00100000
#define NOVA_EDU_SPI       37

/* Guest DTB: each guest's configuration blob (built by tools/yml2dtb)
 * is copied to the top of that guest's configured window before the
 * pristine snapshot, and its IPA is handed to the boot vCPU in x0
 * (Linux boot protocol shape). The runtime guest table computes the
 * per-guest IPA (window end - this reservation); the demo linker
 * script derives its link-time __stack_top from the same reservation
 * at the minimum (NOVA_GUEST_IPA_SIZE) window top. */
#define NOVA_GUEST_DTB_SIZE 0x00002000 /* 8 KiB */

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_GUEST_LAYOUT_H */
