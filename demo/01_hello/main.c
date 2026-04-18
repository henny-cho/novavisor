// Phase 5 demo: first EL1 guest.
//
// Prints a greeting via HVC_PUTS and exits. The mere fact this program
// reaches main() and observes its output confirms that Stage 2 MMU,
// VCPU context switch, and HVC dispatch are all working end-to-end.

#include "demo_hvc.h"

int main(void) {
  hvc_puts_lit("Hello from EL1 guest!\n");
  return 0;
}
