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
//      trapped); each expiry arrives as vINTID 27, and the handler
//      re-arms, which also clears the hypervisor's IMASK.
//
// Five observed ticks prove the whole chain. Then the guest turns off
// what it turned on, which is the only way it can end on a composition
// that serves no exit hypercall: a wfi with something still deliverable
// is architecturally a NOP, so a source left on turns the startup
// stub's idle loop into a spin.

#include "demo_hvc.h"
#include "gic_el1.h"

#include <stdint.h>

#define VTIMER_INTID NOVA_TIMER_VINTID
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

static inline void cntv_disarm(void) {
  const uint64_t off = 0;
  __asm__ volatile("msr cntv_ctl_el0, %0" ::"r"(off));
  __asm__ volatile("isb");
}

// Called from vectors.S with the vINTID already acked. Re-arming is a
// decision rather than a reflex: a handler that always re-arms can
// never be the last one, and the guest would have no quiet moment to
// wind down in.
void demo_irq(uint32_t intid) {
  (void)intid;
  g_tick = g_tick + 1;
  if (g_tick < TICKS) {
    cntv_arm();
  } else {
    cntv_disarm();
  }
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
  // The timer stopped itself on the last tick; close the delivery gate
  // too, so nothing is left pending for the idle loop below to wake on.
  gicr_disable(VTIMER_INTID);
  return 0;
}
