/* nova/abi/guest_layout.h
 *
 * Guest memory window shared by the selected project configuration and
 * guest linker script for Stage 2 mapping, entry, stack, and DTB placement.
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

/* Guest PA windows are packed: guest i starts where guest i-1 ended,
 * rounded up to this alignment (keeps every window Block-mappable and
 * slot 0 identity with the IPA window). A demo manifest's
 * guests[].load_addr must equal the packed PA tools/yml2dtb computes. */
#define NOVA_GUEST_PA_ALIGN 0x00200000 /* 2 MiB */

/* IVC shared page: one 4 KiB page mapped RW (XN) into every VM at the
 * same IPA. It caps the packed guest-window region (0x5000_0000..here,
 * 256 MiB — enforced by tools/yml2dtb), so no window can overlap it;
 * IPA equals PA, keeping slot 0 identity. */
#define NOVA_IVC_SHM_IPA  0x60000000
#define NOVA_IVC_SHM_PA   0x60000000
#define NOVA_IVC_SHM_SIZE 0x00001000 /* 4 KiB */

/* Pristine image area: at boot the hypervisor copies each guest window
 * here (slots packed at the running sum of window sizes), and a warm
 * reset copies it back — guests dirty their .data/BSS/stack, so
 * reseeding the CPU context alone cannot reboot them. EL2-only: never
 * mapped into Stage 2, so no guest can corrupt its own reset image.
 * Sits above the IVC page; tools/yml2dtb bounds the copies to RAM end. */
#define NOVA_GUEST_PRISTINE_PA 0x60100000

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
