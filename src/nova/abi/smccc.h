/* nova/abi/smccc.h
 *
 * SMCCC Arch service function IDs and return codes (Arm DEN0028) — the
 * range guests probe for firmware capabilities that are not PSCI:
 * calling-convention version, feature discovery, SoC identity, and the
 * CPU speculation workarounds.
 *
 * Why the hypervisor must own this: guest Linux gates all of SMCCC 1.1
 * on PSCI_FEATURES(SMCCC_VERSION). Answering NOT_SUPPORTED there makes
 * the guest fall back to 1.0, which has no conduit for the workaround
 * calls — so on a real Neoverse part the guest's Spectre-v2 and BHB
 * firmware mitigations silently do nothing and sysfs reports
 * "Vulnerable". QEMU's TCG CPU hides this because the guest sees no
 * affected MIDR.
 *
 * Plain #defines only: this header must survive the assembler and the
 * C/C++ compilers alike.
 */

#ifndef NOVA_SMCCC_H
#define NOVA_SMCCC_H

// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#define SMCCC_FN_VERSION       0x80000000
#define SMCCC_FN_ARCH_FEATURES 0x80000001
#define SMCCC_FN_ARCH_SOC_ID   0x80000002
#define SMCCC_FN_WORKAROUND_3  0x80003FFF
#define SMCCC_FN_WORKAROUND_2  0x80007FFF
#define SMCCC_FN_WORKAROUND_1  0x80008000

/* Version 1.1: the minimum that carries the workaround calls. */
#define SMCCC_VERSION_1_1 0x00010001

/* Return codes shared with PSCI's, plus NOT_REQUIRED — the answer that
 * tells a guest "this CPU is not affected, stop calling". */
#define SMCCC_SUCCESS       0
#define SMCCC_NOT_SUPPORTED (-1)
#define SMCCC_NOT_REQUIRED  (-2)

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_SMCCC_H */
