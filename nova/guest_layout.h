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
#define NOVA_GUEST_IPA_BASE 0x50000000
#define NOVA_GUEST_IPA_SIZE 0x00100000 /* 1 MiB */
// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_GUEST_LAYOUT_H */
