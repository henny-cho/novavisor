/* nova/trap_context_offsets.h
 *
 * Byte offsets and total size of the TrapContext frame — the single
 * source of truth for the C++ <-> assembly trap ABI. Included by both
 * hal/armv8_aarch64/vec.S (frame construction) and nova/trap_context.hpp
 * (struct layout + static_asserts), so the two can never drift apart.
 *
 * Plain #defines only: this header must survive the assembler and the
 * C++ compiler alike.
 */

#ifndef NOVA_TRAP_CONTEXT_OFFSETS_H
#define NOVA_TRAP_CONTEXT_OFFSETS_H

/* Macros are the point here (assembler/linker-script consumers) — the
 * usual constexpr guidance does not apply. */
// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)
#define NOVA_CTX_OFF_X30   240
#define NOVA_CTX_OFF_SP    248
#define NOVA_CTX_OFF_ELR   256
#define NOVA_CTX_OFF_SPSR  264
#define NOVA_CTX_OFF_ESR   272
#define NOVA_CTX_OFF_FAR   280
#define NOVA_TRAP_CTX_SIZE 288
// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_TRAP_CONTEXT_OFFSETS_H */
