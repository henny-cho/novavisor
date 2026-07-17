/* nova/hvc_abi.h
 *
 * Guest <-> hypervisor HVC function IDs — the single source of truth for
 * the hypercall ABI. Included by the hypervisor dispatcher
 * (components/demo_hvc), the guest-side stubs (demo/common/include/
 * demo_hvc.h), and guest assembly (demo/common/startup.S).
 *
 * Calling convention (SMCCC-style): function ID in x0, arguments in
 * x1..x6, return value (if any) in x0, issued via `hvc #0`.
 *
 * ID allocation (one 0x100 range per subsystem; a subscriber must ignore
 * IDs outside its range — see components/trap_handler/):
 *   0x1000..0x10FF  demo    (PUTS/PUTC/EXIT/...)
 *   0x1100..0x11FF  ivc     (Phase 7+)
 *   0x1200..0x12FF  timer   (Phase 6+)
 *
 * Plain #defines only: this header must survive the assembler and the
 * C/C++ compilers alike.
 */

#ifndef NOVA_HVC_ABI_H
#define NOVA_HVC_ABI_H

/* Macros are the point here (assembler/linker-script consumers) — the
 * usual constexpr guidance does not apply. */
// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)
#define NOVA_HVC_FN_PUTS      0x1000
#define NOVA_HVC_FN_PUTC      0x1001
#define NOVA_HVC_FN_EXIT      0x1002
#define NOVA_HVC_FN_YIELD     0x1003
#define NOVA_HVC_FN_HEARTBEAT 0x1004

/* IVC range (Phase 7) */
#define NOVA_HVC_FN_IVC_DOORBELL 0x1100

/* Timer range (Phase 6) */
#define NOVA_HVC_FN_TIMER_SET 0x1200
// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_HVC_ABI_H */
