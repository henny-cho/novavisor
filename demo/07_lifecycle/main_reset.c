// Phase 11 demo, reset half: PSCI SYSTEM_RESET self-reboot.
//
// A warm reset reloads the pristine guest image, so this guest cannot
// remember its own run in .data/BSS — the boot counter lives in the
// IVC shared page, which is outside the reset scope (and proves that
// only the guest window is rolled back). Run 1 launches the sibling
// and reboots itself through the standard PSCI path; run 2 exits.

#include "demo_hvc.h"
#include "guest_psci.h"
#include "nova/abi/guest_layout.h"

#include <stdint.h>

// Guest-owned boot counters at the tail of the shared page (the IVC
// mailbox protocol owns the head), indexed by guest_table slot.
#define RUN_COUNT ((volatile uint64_t*)(NOVA_IVC_SHM_IPA + 0xF80))

int main(void) {
  const uint64_t run = ++RUN_COUNT[0];

  hvc_puts_lit("A: boot ");
  hvc_putc((char)('0' + run));
  hvc_putc('\n');

  if (run == 1) {
    hvc_vm_start(1);     // launch the hang/recovery sibling first
    psci_system_reset(); // reboot ourselves — resumes at _start
  }

  hvc_puts_lit("A: done\n");
  return 0;
}
