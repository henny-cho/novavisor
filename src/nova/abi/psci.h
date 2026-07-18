/* nova/abi/psci.h
 *
 * PSCI function IDs and return codes (Arm DEN0022, SMCCC DEN0028) — the
 * single source shared by the hypervisor's PSCI implementation
 * (components/psci) and the guest-side stubs (demo/common/include/
 * guest_psci.h). Conduit is HVC: this board has no EL3, so SMC from
 * EL1 is UNDEFINED.
 *
 * Plain #defines only: this header must survive the assembler and the
 * C/C++ compilers alike.
 */

#ifndef NOVA_PSCI_H
#define NOVA_PSCI_H

/* Macros are the point here (assembler/guest-C consumers) — the usual
 * constexpr guidance does not apply. */
// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

/* SMC32 function IDs; the SMC64 twin (where one exists) sets bit 30. */
#define PSCI_FN_VERSION       0x84000000
#define PSCI_FN_CPU_OFF       0x84000002
#define PSCI_FN_CPU_ON        0x84000003
#define PSCI_FN_AFFINITY_INFO 0x84000004
#define PSCI_FN_SYSTEM_OFF    0x84000008
#define PSCI_FN_SYSTEM_RESET  0x84000009
#define PSCI_FN_FEATURES      0x8400000A
#define PSCI_FN_SMC64         0x40000000 /* OR into an ID for the SMC64 form */

#define PSCI_VERSION_1_1 0x00010001

/* AFFINITY_INFO return states */
#define PSCI_AFFINITY_ON  0
#define PSCI_AFFINITY_OFF 1

/* Return codes (int32, sign-extended into x0) */
#define PSCI_SUCCESS            0
#define PSCI_NOT_SUPPORTED      (-1)
#define PSCI_INVALID_PARAMETERS (-2)

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_PSCI_H */
