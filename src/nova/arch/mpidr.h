/* nova/arch/mpidr.h
 *
 * Affinity field mask in the MPIDR/IROUTER representation: Aff3 in
 * bits 39:32, Aff2..Aff0 in bits 23:0.
 *
 * Plain #define only: shared by the boot assembly (hal/arch/aarch64/
 * boot.S) and the C++ per-core identity helpers.
 */

#ifndef NOVA_ARCH_MPIDR_H
#define NOVA_ARCH_MPIDR_H

/* Macros are the point here (assembly consumer) — the usual constexpr
 * guidance does not apply. */
// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#define NOVA_MPIDR_AFFINITY_MASK 0x000000FF00FFFFFF

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_ARCH_MPIDR_H */
