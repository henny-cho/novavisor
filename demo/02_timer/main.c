// Phase 6 demo: one-shot virtual timer interrupt.
//
// Requests a ~100 ms one-shot from the hypervisor via HVC_TIMER_SET,
// then waits in WFI until the injected virtual timer PPI (vINTID 27)
// arrives through the guest vector table (vectors.S). Reaching the
// final HVC_PUTS proves the whole Phase 6 chain: GICv3 bring-up, EL2
// IRQ trap, List Register injection, and the guest-side ICV handshake.
//
// Since the vGIC became the delivery authority (Phase 8), the guest
// must enable PPI 27 at its emulated redistributor before arming.

#include "demo_hvc.h"
#include "gic_el1.h"

#include <stdint.h>

extern char       _demo_vectors[]; // vectors.S
volatile uint64_t g_irq_count = 0; // bumped by the IRQ handler

static inline uint64_t read_cntfrq(void) {
  uint64_t v;
  __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(v));
  return v;
}

int main(void) {
  __asm__ volatile("msr vbar_el1, %0" ::"r"(_demo_vectors));

  gicr_wake();
  gicr_enable(NOVA_TIMER_VINTID); // vINTID injected by HVC_TIMER_SET

  __asm__ volatile("msr daifclr, #2"); // unmask IRQ (vIRQ under HCR_EL2.IMO)

  if (hvc_timer_set(read_cntfrq() / 10) != 0) { // ~100 ms
    hvc_puts_lit("timer_set failed\n");
    return 1;
  }

  while (g_irq_count == 0) {
    __asm__ volatile("wfi");
  }

  hvc_puts_lit("timer_fired\n");
  return 0;
}
