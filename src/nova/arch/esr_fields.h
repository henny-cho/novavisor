/* nova/arch/esr_fields.h
 *
 * ESR_EL2 field positions (Arm ARM D17.2.37) — the architecture facts
 * behind esr.hpp's parsing helpers, split out so a reader that is not a
 * C++ compiler can have them too.
 *
 *   [31:26]  EC  — Exception Class
 *   [25]     IL  — Instruction Length: 1 = 32-bit, 0 = 16-bit
 *   [24:0]   ISS — Instruction-Specific Syndrome
 *
 * Today's second reader is the workbench bridge, which decodes a
 * trapped syndrome out of guest RAM and would otherwise carry its own
 * copy of these shifts. The class names are not here: those come from
 * esr.hpp's own enum through DWARF, which is a definition the bridge
 * can already read.
 *
 * Plain #defines only: this header must survive the assembler and the
 * C/C++ compilers alike.
 */

#ifndef NOVA_ESR_FIELDS_H
#define NOVA_ESR_FIELDS_H

/* Macros are the point here (non-C++ consumers) — the usual constexpr
 * guidance does not apply. */
// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#define NOVA_ESR_EC_SHIFT 26
#define NOVA_ESR_EC_MASK  0x3F
#define NOVA_ESR_IL_SHIFT 25
#define NOVA_ESR_ISS_MASK 0x01FFFFFF

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_ESR_FIELDS_H */
