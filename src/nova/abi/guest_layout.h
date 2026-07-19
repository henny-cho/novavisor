/* nova/abi/guest_layout.h
 *
 * Phase 5 guest memory window — the single source of truth shared by:
 *   - projects/qemu_virt_arm64/include/guest_config.hpp (Stage 2 map,
 *     ELR/SP_EL1 seeding)
 *   - demo/common/linker.ld.S (guest link base; preprocessed at build)
 *
 * The QEMU loader address in each demo's manifest.yml (guests[].load_addr)
 * must match NOVA_GUEST_IPA_BASE — the demo runner consumes the manifest
 * at runtime, so it cannot include this header.
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

/* PA slot for guest i: NOVA_GUEST_IPA_BASE + i * NOVA_GUEST_PA_STRIDE.
 * Slot 0 is identity (Phase 5/6 single-guest demos stay unchanged).
 * The 2 MiB stride keeps slot bases Block-alignable for future window
 * growth. A demo manifest's guests[].load_addr must equal the slot PA. */
#define NOVA_GUEST_PA_STRIDE 0x00200000 /* 2 MiB */

/* IVC shared page: one 4 KiB page mapped RW (XN) into every VM at the
 * same IPA, just above the guest window. The PA sits past the last
 * guest PA slot (4 slots x 2 MiB from 0x5000_0000). */
#define NOVA_IVC_SHM_IPA  0x50100000
#define NOVA_IVC_SHM_PA   0x50800000
#define NOVA_IVC_SHM_SIZE 0x00001000 /* 4 KiB */

/* Pristine image area: at boot the hypervisor copies each guest window
 * here (one NOVA_GUEST_IPA_SIZE slot per guest), and a warm reset
 * copies it back — guests dirty their .data/BSS/stack, so reseeding
 * the CPU context alone cannot reboot them. EL2-only: never mapped
 * into Stage 2, so no guest can corrupt its own reset image. */
#define NOVA_GUEST_PRISTINE_PA 0x50A00000

/* Emulated GICv3 frames: left unmapped in Stage 2 so every access traps
 * into the vGIC. The IPAs equal the board's physical GIC addresses so
 * the guest sees the memory map a DTB would advertise. Register offsets
 * within the frames come from nova/arch/gicv3_regs.h. */
#define NOVA_GICD_IPA_BASE 0x08000000
#define NOVA_GICR_IPA_BASE 0x080A0000

/* Emulated PL011 (vuart): same unmapped-frame technique, IPA equal to
 * the board's physical UART so guests run an unmodified virt-map
 * driver. RX delivery uses the same SPI number the physical UART has. */
#define NOVA_VUART_IPA_BASE 0x09000000
#define NOVA_VUART_IPA_SIZE 0x00001000
#define NOVA_VUART_SPI      33

/* Guest DTB: each guest's configuration blob (built by tools/yml2dtb)
 * is copied into the top of the linker window before the pristine
 * snapshot, and its IPA is handed to the boot vCPU in x0 (Linux boot
 * protocol shape). The reservation sits at the fixed 1 MiB window top
 * regardless of the configured memory size, so every valid config
 * contains it; the stack top moves down below it. */
#define NOVA_GUEST_DTB_SIZE 0x00002000 /* 8 KiB */
#define NOVA_GUEST_DTB_IPA  (NOVA_GUEST_IPA_BASE + NOVA_GUEST_IPA_SIZE - NOVA_GUEST_DTB_SIZE)

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_GUEST_LAYOUT_H */
