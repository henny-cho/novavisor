// Phase 11 demo, hang half: watchdog recovery.
//
// Run 1 keeps a 500 ms watchdog window fed three times, then goes
// silent in a busy loop — a simulated hang that never traps. Only the
// hypervisor's watchdog can recover it: the missed window warm-resets
// the VM, and run 2 (told apart by the shared-page boot counter — the
// guest window itself comes back pristine) completes and powers off
// through PSCI SYSTEM_OFF.

#include "demo_hvc.h"
#include "guest_psci.h"
#include "nova/abi/guest_layout.h"

#include <stdint.h>

#define RUN_COUNT ((volatile uint64_t*)(NOVA_IVC_SHM_IPA + 0xF80))
#define WINDOW_MS 500

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

static void spin_ms(uint64_t ms) {
  const uint64_t until = read_cntvct() + read_cntfrq() * ms / 1000;
  while (read_cntvct() < until) {
    // burn virtual time — no yield, no wfi
  }
}

int main(void) {
  const uint64_t run = ++RUN_COUNT[1];

  hvc_puts_lit("B: boot ");
  hvc_putc((char)('0' + run));
  hvc_putc('\n');

  for (int i = 0; i < 3; ++i) {
    hvc_heartbeat(WINDOW_MS);
    spin_ms(100);
  }

  if (run == 1) {
    for (;;) {
      // hang: no heartbeat, no trap — only the watchdog can save us
    }
  }

  hvc_heartbeat(0); // healthy this time — disarm before powering off
  hvc_puts_lit("B: done\n");
  psci_system_off();
}
