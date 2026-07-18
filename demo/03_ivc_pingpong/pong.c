// Phase 7 demo, responder VM (guest_table index 1).
//
// Started by ping via HVC_VM_START. Echoes every mailbox message back
// and rings ping's doorbell, until the message carries the last-round
// flag — then exits cleanly (the scheduler keeps ping running).

#include "demo_hvc.h"
#include "ivc_shm.h"

#include <stdint.h>

#define PING_VM 0

extern char       _demo_vectors[]; // vectors.S
volatile uint64_t g_irq_count = 0; // bumped by the doorbell vIRQ handler

int main(void) {
  __asm__ volatile("msr vbar_el1, %0" ::"r"(_demo_vectors));
  __asm__ volatile("msr daifclr, #2"); // unmask IRQ (vIRQ under HCR_EL2.IMO)

  struct ivc_mailbox* rx = &IVC_SHM->box[1];       // pings from ping
  struct ivc_mailbox* tx = &IVC_SHM->box[PING_VM]; // echoes to ping

  for (uint64_t round = 1;; ++round) {
    while (rx->seq < round) {
      hvc_yield();
    }
    const uint64_t msg  = rx->payload[0];
    const uint64_t last = rx->payload[1];

    ivc_send(tx, msg, 0);
    hvc_ivc_doorbell(PING_VM);

    if (last != 0) {
      return 0;
    }
  }
}
