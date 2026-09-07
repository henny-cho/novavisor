// Interrupt-latency sample volume.
//
// The isolation SLO judges a p99.9 of "periodic timer fires -> handler
// entry", and neither end is visible to EL2: the entry happens inside
// the guest, and a private timer vIRQ leaves no completion record to
// stand in for it. So the guest measures both and reports them, once
// per interrupt, a hundred thousand times.
//
// Not part of the demo suite (manifest.enabled=false). It demonstrates
// nothing; it exists to be measured, and its output is one line.

#include "demo_hvc.h"
#include "gic_el1.h"

#include <stdint.h>

// What p99.9 needs to mean anything: a hundred samples in the top tenth
// of a percent. The period is half of what this path was measured to
// service (10.7 kHz under QEMU) — past that a deadline is already gone
// when it is armed and the timer free-runs instead of sampling a period.
#define SAMPLES   100000
#define PERIOD_HZ 5000

extern char _demo_vectors[]; // vectors.S

static uint64_t g_deadline; // the comparator value the next sample starts at
static uint64_t g_period;
static uint64_t g_count;

static inline uint64_t read_cntfrq(void) {
  uint64_t v;
  __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(v));
  return v;
}

static inline uint64_t read_cntvct(void) {
  uint64_t v;
  __asm__ volatile("isb; mrs %0, cntvct_el0" : "=r"(v));
  return v;
}

// An absolute deadline, not a countdown: the value reported as the
// sample's start has to be the one the comparator held. Writing CTL
// also clears the IMASK the hypervisor set when it forwarded the expiry.
static inline void arm(uint64_t deadline) {
  __asm__ volatile("msr cntv_cval_el0, %0" ::"r"(deadline));
  __asm__ volatile("msr cntv_ctl_el0, %0" ::"r"(1UL));
  __asm__ volatile("isb");
}

// Called from vectors.S with the acked vINTID and the counter read at
// entry. Reporting comes first so the hypercall is outside the sample.
void demo_irq(uint32_t intid, uint64_t entry) {
  hvc_irq_sample(intid, g_deadline, entry);
  g_deadline += g_period;
  arm(g_deadline);
  if (++g_count == SAMPLES) {
    __asm__ volatile("msr cntv_ctl_el0, xzr"); // nothing left to sample
    hvc_puts_lit("irq_latency: ");
    hvc_put_dec(g_count);
    hvc_putc('\n');
    hvc_exit(0);
  }
}

int main(void) {
  __asm__ volatile("msr vbar_el1, %0" ::"r"(_demo_vectors));

  gicd_enable_group1();
  gicr_wake();
  gicr_enable(NOVA_TIMER_VINTID);
  icc_init();

  __asm__ volatile("msr daifclr, #2"); // unmask IRQ

  g_period   = read_cntfrq() / PERIOD_HZ;
  g_deadline = read_cntvct() + g_period;
  arm(g_deadline);

  // Spin, not wfi: a blocked vCPU has its CNTV mirrored into a soft
  // timer and woken from there, which is a different path with a
  // different latency and a different record for every sample.
  for (;;) {
  }
}
