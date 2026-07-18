// Phase 9 demo, sleeper half: wfi-driven periodic ticker.
//
// Standard-path setup as in demo 04 (VBAR, emulated GICR enable for
// PPI 27, ICV init), then five 400 ms CNTV periods with wfi waits in
// between. Every wait exercises the wfi trap: while the busy sibling
// runs, a blocked-VCPU wake via the hypervisor's mirrored CNTV
// deadline; after the sibling exits, the EL2 idle loop as well.
//
// Virtual-time schedule vs the sibling: tick 2 (0.8 s) lands before
// "busy done" (1.0 s) only if preemption lets this guest run at all,
// and ticks 3–5 (1.2–2.0 s) complete only if wake-from-idle works.

#include "demo_hvc.h"
#include "gic_el1.h"

#include <stdint.h>

#define TICKS 5

extern char       _demo_vectors[]; // vectors.S
volatile uint64_t g_tick = 0;      // bumped by the IRQ handler

// Read by the IRQ handler to re-arm the next period.
uint64_t g_period_ticks = 0;

static inline uint64_t read_cntfrq(void) {
  uint64_t v;
  __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(v));
  return v;
}

static inline void cntv_arm(uint64_t ticks) {
  __asm__ volatile("msr cntv_tval_el0, %0" ::"r"(ticks));
  uint64_t enable = 1;
  __asm__ volatile("msr cntv_ctl_el0, %0" ::"r"(enable));
  __asm__ volatile("isb");
}

int main(void) {
  __asm__ volatile("msr vbar_el1, %0" ::"r"(_demo_vectors));

  gicd_enable_group1();
  gicr_wake();
  gicr_enable(NOVA_TIMER_VINTID);
  icc_init();

  __asm__ volatile("msr daifclr, #2"); // unmask IRQ

  g_period_ticks = read_cntfrq() * 2 / 5; // 400 ms per tick
  cntv_arm(g_period_ticks);

  uint64_t seen = 0;
  while (seen < TICKS) {
    while (g_tick == seen) {
      __asm__ volatile("wfi");
    }
    seen = g_tick;
    hvc_puts_lit("wfi tick ");
    hvc_putc((char)('0' + seen));
    hvc_putc('\n');
  }

  hvc_puts_lit("preempt: wfi done\n");
  return 0;
}
