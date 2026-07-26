// Phase 25 demo: panic-path smoke.
//
// Asks EL2 to fault itself (W^X write into its own .rodata) so the
// fatal-vector identification, first-failure panic protocol, and the
// extended register dump run for real — no other demo ever takes this
// path, so without this the report machinery would first execute on a
// real board's first bad day.

#include "demo_hvc.h"

int main(void) {
  hvc_puts_lit("panic smoke: triggering an EL2 fault\n");
  hvc_diag_el2_fault();
  return 1; // unreachable — the machine halts inside EL2
}
