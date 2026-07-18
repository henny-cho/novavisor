// Phase 8 demo: architecture-standard interrupt and timer path.
//
// Apart from console output and exit (HVC PUTS/EXIT), this guest uses
// no paravirtual services — it does what an unmodified OS does at boot:
//   1. install a vector table (VBAR_EL1),
//   2. program the distributor/redistributor over MMIO (trapped and
//      emulated by the hypervisor's vGIC): wake handshake, group,
//      priority, enable for the virtual timer PPI 27,
//   3. initialize the CPU interface (ICC_* — hardware-virtualized ICV),
//   4. drive a periodic timer directly from CNTV_CTL/TVAL (never
//      trapped); each expiry arrives as vINTID 27 and the IRQ handler
//      (vectors.S) re-arms, which also clears the hypervisor's IMASK.
//
// Five observed ticks prove the whole chain.

#include "demo_hvc.h"
#include "gic_el1.h"

#include <stdint.h>

#define VTIMER_INTID 27
#define TICKS        5

extern char       _demo_vectors[]; // vectors.S
volatile uint64_t g_tick = 0;      // bumped by the IRQ handler

static inline uint64_t read_cntfrq(void) {
  uint64_t v;
  __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(v));
  return v;
}

// Arm the virtual timer ~8 ms out (frequency / 128). Writing CTL=ENABLE
// also clears IMASK, which the hypervisor sets when it forwards an
// expiry of this level-triggered timer.
static inline void cntv_arm(void) {
  uint64_t v = read_cntfrq() >> 7;
  __asm__ volatile("msr cntv_tval_el0, %0" ::"r"(v));
  v = 1; // ENABLE
  __asm__ volatile("msr cntv_ctl_el0, %0" ::"r"(v));
  __asm__ volatile("isb");
}

int main(void) {
  __asm__ volatile("msr vbar_el1, %0" ::"r"(_demo_vectors));

  gicd_enable_group1();
  gicr_wake();
  gicr_enable(VTIMER_INTID);
  icc_init();

  __asm__ volatile("msr daifclr, #2"); // unmask IRQ

  cntv_arm();

  uint64_t seen = 0;
  while (seen < TICKS) {
    while (g_tick == seen) {
      __asm__ volatile("wfi");
    }
    seen = g_tick;
    hvc_puts_lit("native tick ");
    hvc_putc((char)('0' + seen));
    hvc_putc('\n');
  }
  return 0;
}
