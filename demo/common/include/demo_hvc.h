// NovaVisor demo HVC helpers.
//
// Inline stubs for the NovaVisor guest hypercall ABI. Shared by every
// demo guest. Function IDs come from nova/abi/hvc_abi.h — the single source
// shared with the hypervisor's dispatcher (the demo_hvc component). The
// register contract is the conduit's, in guest_smccc.h.

#ifndef NOVAVISOR_DEMO_HVC_H
#define NOVAVISOR_DEMO_HVC_H

#include "guest_smccc.h"
#include "nova/abi/hvc_abi.h"

#include <stddef.h>
#include <stdint.h>

enum {
  HVC_PUTS      = NOVA_HVC_FN_PUTS,
  HVC_PUTC      = NOVA_HVC_FN_PUTC,
  HVC_EXIT      = NOVA_HVC_FN_EXIT,
  HVC_YIELD     = NOVA_HVC_FN_YIELD,
  HVC_HEARTBEAT = NOVA_HVC_FN_HEARTBEAT,
  HVC_VM_START  = NOVA_HVC_FN_VM_START,
  // IVC range (Phase 7)
  HVC_IVC_DOORBELL = NOVA_HVC_FN_IVC_DOORBELL,
  // Timer range (Phase 6)
  HVC_TIMER_SET = NOVA_HVC_FN_TIMER_SET,
  // EL2-owned DMA test device
  HVC_DMA_FAULT_INJECT = NOVA_HVC_FN_DMA_FAULT_INJECT,
  // Diagnostics (demo builds only)
  HVC_DIAG_EL2_FAULT  = NOVA_HVC_FN_DIAG_EL2_FAULT,
  HVC_DIAG_IRQ_SAMPLE = NOVA_HVC_FN_DIAG_IRQ_SAMPLE,
};

static inline void hvc_putc(char c) {
  (void)smccc_call(HVC_PUTC, (uint64_t)(unsigned char)c, 0, 0);
}

static inline void hvc_puts(const char* s, size_t n) {
  (void)smccc_call(HVC_PUTS, (uint64_t)(uintptr_t)s, (uint64_t)n, 0);
}

// Convenience: print a C string literal whose length the compiler knows.
#define hvc_puts_lit(s) hvc_puts((s), sizeof(s) - 1)

// Decimal, no padding. Here rather than in each guest that counts
// something: three copies of this loop were three chances to disagree
// about what a number looks like in the log a test greps.
static inline void hvc_put_dec(uint64_t v) {
  char   buf[20];
  size_t i = sizeof(buf);
  do {
    buf[--i] = (char)('0' + (v % 10U));
    v /= 10U;
  } while (v != 0U);
  hvc_puts(&buf[i], sizeof(buf) - i);
}

static inline void hvc_exit(int code) {
  (void)smccc_call(HVC_EXIT, (uint64_t)code, 0, 0);
  smccc_park();
}

// Report one interrupt-latency sample: the deadline this guest armed and
// the counter it read on handler entry, both virtual. Called after the
// read, so the hypercall's own cost is outside the sample.
static inline void hvc_irq_sample(uint64_t vintid, uint64_t deadline, uint64_t entry) {
  (void)smccc_call(HVC_DIAG_IRQ_SAMPLE, vintid, deadline, entry);
}

// Ask EL2 to fault itself (panic-path smoke). Never returns.
static inline void hvc_diag_el2_fault(void) {
  (void)smccc_call(HVC_DIAG_EL2_FAULT, 0, 0, 0);
  smccc_park();
}

static inline void hvc_yield(void) {
  (void)smccc_call(HVC_YIELD, 0, 0, 0);
}

// Re-arm the caller's watchdog: a warm reset follows if the next
// heartbeat does not arrive within `window_ms`. 0 disarms.
static inline void hvc_heartbeat(uint64_t window_ms) {
  (void)smccc_call(HVC_HEARTBEAT, window_ms, 0, 0);
}

// One-shot hypervisor timer: injects vINTID 27 (virtual timer PPI)
// after `ticks` counter cycles (CNTFRQ rate). Returns 0 on success.
static inline uint64_t hvc_timer_set(uint64_t ticks) {
  return (uint64_t)smccc_call(HVC_TIMER_SET, ticks, 0, 0);
}

// Start a not-yet-running VM (guest_table index). The new VM runs when
// someone yields. Returns 0 on success.
static inline uint64_t hvc_vm_start(uint64_t vm_index) {
  return (uint64_t)smccc_call(HVC_VM_START, vm_index, 0, 0);
}

// Ring the doorbell of another VM: injects the doorbell vIRQ
// (vINTID NOVA_IVC_DOORBELL_VINTID) into it. Returns 0 on success.
static inline uint64_t hvc_ivc_doorbell(uint64_t vm_index) {
  return (uint64_t)smccc_call(HVC_IVC_DOORBELL, vm_index, 0, 0);
}

// Request one DMA beyond the caller's assigned window. Returns 0 when
// the EL2-owned test device accepted the request.
static inline uint64_t hvc_dma_fault_inject(void) {
  return (uint64_t)smccc_call(HVC_DMA_FAULT_INJECT, 0, 0, 0);
}

#endif // NOVAVISOR_DEMO_HVC_H
