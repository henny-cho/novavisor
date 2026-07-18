// Phase 9 demo, busy half: a deliberately non-cooperative guest.
//
// After one management call to launch its sibling (HVC_VM_START), this
// guest never yields, never sleeps, never traps until it is done: it
// burns through five 200 ms intervals of VIRTUAL time (cntvct — the
// same clock the sibling's CNTV deadlines use, so the demo's expected
// output order is deterministic regardless of host speed). Without
// time-slice preemption the sibling would starve until this guest
// exits; its ticks appearing in between prove the slice works.

#include "demo_hvc.h"

#include <stdint.h>

#define INTERVALS 5

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

int main(void) {
  hvc_vm_start(1); // wake the sleeper, then stop cooperating

  const uint64_t interval = read_cntfrq() / 5; // 200 ms of virtual time
  uint64_t       deadline = read_cntvct() + interval;

  for (unsigned i = 1; i <= INTERVALS; ++i) {
    while (read_cntvct() < deadline) {
      // burn — no yield, no wfi
    }
    deadline += interval;
    hvc_puts_lit("busy ");
    hvc_putc((char)('0' + i));
    hvc_putc('\n');
  }

  hvc_puts_lit("preempt: busy done\n");
  return 0;
}
