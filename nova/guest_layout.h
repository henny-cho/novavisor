/* nova/guest_layout.h
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

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_GUEST_LAYOUT_H */
