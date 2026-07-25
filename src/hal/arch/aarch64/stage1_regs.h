/* hal/arch/aarch64/stage1_regs.h
 *
 * EL2 Stage-1 translation register values — the single source shared
 * by boot.S (secondary entry enables the MMU before any C++ or stack
 * access) and stage1_tables.hpp (which re-derives them from named
 * fields and static_asserts equality).
 *
 * Plain #defines only: this header must survive the assembler's C
 * preprocessor.
 */

#ifndef NOVA_STAGE1_REGS_H
#define NOVA_STAGE1_REGS_H

// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

/* MAIR_EL2: Attr0 = Device-nGnRE (0x04), Attr1 = Normal WB RA WA (0xFF). */
#define NOVA_EL2_MAIR 0xFF04

/* TCR_EL2 (non-E2H): RES1 bits 31/23, PS=32-bit PA, TG0=4K,
 * SH0=inner shareable, ORGN0=IRGN0=WB RA WA cacheable walks, T0SZ=32. */
#define NOVA_EL2_TCR 0x80803520

/* SCTLR_EL2: RES1 0x30C50830 | M | C | I | WXN. Written as a whole so
 * the configuration never depends on what firmware left behind. */
#define NOVA_EL2_SCTLR 0x30CD1835

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_STAGE1_REGS_H */
