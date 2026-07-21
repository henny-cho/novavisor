// Phase 13 demo: one VM running as an SMP guest.
//
// The boot vCPU powers its sibling on with PSCI CPU_ON (entry =
// common/secondary.S, context_id = a private stack top), each vCPU
// wakes its own redistributor frame, and the two exchange 100 IPI
// round-trips through trapped ICC_SGI1R writes — vCPU 0 on physical
// core 0, vCPU 1 on core 1, truly in parallel. The first run resets
// while vCPU 1 is active, proving EL2 waits for its quiesce ACK before
// restoring RAM. The second run exercises CPU_OFF + AFFINITY_INFO.
//
// Distinct INTIDs per direction (PING to vCPU 1, PONG to vCPU 0) keep
// each side acking only its own SGIs. One-shot SGI wake-ups use the
// mask-check-wfi pattern: an IRQ landing between the flag check and
// the wfi would otherwise be consumed early and never wake anyone.

#include "demo_hvc.h"
#include "gic_el1.h"
#include "guest_psci.h"
#include "nova/abi/guest_layout.h"

#include <stdint.h>

#define SGI_PING 1 /* vCPU 0 -> vCPU 1 */
#define SGI_PONG 2 /* vCPU 1 -> vCPU 0 */
#define ROUNDS   100

// Outside the reset guest window, so it survives the pristine-image
// restore while g_ready/g_done/g_pongs below return to zero.
#define RUN_COUNT ((volatile uint64_t*)(NOVA_IVC_SHM_IPA + 0xF80))

extern char _demo_vectors[];    // vectors.S
extern char _secondary_start[]; // common/secondary.S
extern char __stack_top[];      // linker script (boot vCPU's stack)

// Shared between the vCPUs — same Stage 2 window, MMU off (Device
// attributes), so plain volatile loads/stores are the whole protocol.
static volatile uint32_t g_ready = 0; // vCPU 1 is IRQ-ready
static volatile uint32_t g_done  = 0; // vCPU 0 -> vCPU 1: retire
static volatile uint64_t g_pongs = 0; // replies seen by vCPU 0's handler

static inline void irq_mask(void) {
  __asm__ volatile("msr daifset, #2");
}

static inline void irq_unmask(void) {
  __asm__ volatile("msr daifclr, #2");
}

// Called from vectors.S with the vINTID already acked (x0). Both vCPUs
// share the vector table; the INTID says which side is running.
void demo_irq(uint32_t intid) {
  if (intid == SGI_PING) {
    icc_send_sgi(1U << 0, SGI_PONG); // echo back to the boot vCPU
  } else if (intid == SGI_PONG) {
    g_pongs = g_pongs + 1;
  }
}

// Block until `*flag` reaches `want`, masking IRQ around each check so
// a wake-up landing after the check still aborts the wfi (the pending
// vIRQ makes the trapped wfi a NOP).
static void wait_pongs(uint64_t want) {
  for (;;) {
    irq_mask();
    if (g_pongs >= want) {
      irq_unmask();
      return;
    }
    __asm__ volatile("wfi");
    irq_unmask(); // a pended reply is taken right here
  }
}

// Secondary vCPU main (from common/secondary.S, own stack, BSS shared).
// Returning powers the vCPU off.
void secondary_main(void) {
  __asm__ volatile("msr vbar_el1, %0" ::"r"(_demo_vectors));
  gicr_wake_at(my_vcpu()); // own redistributor frame (SGIs enabled at reset)
  icc_init();

  hvc_puts_lit("vcpu1 online\n");
  g_ready = 1;

  for (;;) {
    irq_mask();
    if (g_done) {
      irq_unmask();
      return; // secondary.S issues CPU_OFF
    }
    __asm__ volatile("wfi");
    irq_unmask(); // echo happens in the handler on the way through
  }
}

int main(void) {
  const uint64_t run = ++RUN_COUNT[0];

  __asm__ volatile("msr vbar_el1, %0" ::"r"(_demo_vectors));
  gicd_enable_group1();
  gicr_wake_at(my_vcpu());
  icc_init();
  irq_unmask();

  // The sibling gets its own stack, carved below the boot vCPU's.
  const uint64_t sibling_stack = (uint64_t)__stack_top - 0x10000U;
  if (psci_cpu_on(/*mpidr=*/1, (uint64_t)_secondary_start, sibling_stack) != PSCI_SUCCESS) {
    return 1;
  }
  while (!g_ready) {
    // the sibling boots in parallel on the other physical core
  }

  if (run == 1) {
    hvc_puts_lit("guest smp: reset with vcpu1 active\n");
    psci_system_reset();
  }

  for (uint64_t i = 1; i <= ROUNDS; ++i) {
    icc_send_sgi(1U << 1, SGI_PING);
    wait_pongs(i);
  }

  // Round trips done — release the sibling and prove it retired. The
  // extra PING only wakes it to observe g_done (its echo is ignored).
  g_done = 1;
  icc_send_sgi(1U << 1, SGI_PING);
  while (psci_affinity_info(1) != PSCI_AFFINITY_OFF) {
  }

  hvc_puts_lit("guest smp: 100 ipi rounds ok\n");
  return 0;
}
