// Phase 7 demo, initiator VM (guest_table index 0).
//
// Starts the responder VM, then runs 1000 ping-pong rounds: publish a
// message in the shared mailbox, ring the peer's doorbell, and
// poll+yield until the echo arrives. Reaching the final HVC_PUTS proves
// the whole Phase 7 chain: HVC_VM_START, per-VM Stage 2 (both VMs link
// at the same IPA), cooperative VCPU switching, and doorbell vIRQ
// delivery through the LR0 shadow.

#include "demo_hvc.h"
#include "ivc_shm.h"

#include <stdint.h>

#define ROUNDS    1000
#define PONG_VM   1
#define FLAG_LAST 1

extern char       _demo_vectors[]; // vectors.S
volatile uint64_t g_irq_count = 0; // bumped by the doorbell vIRQ handler

int main(void) {
  __asm__ volatile("msr vbar_el1, %0" ::"r"(_demo_vectors));
  __asm__ volatile("msr daifclr, #2"); // unmask IRQ (vIRQ under HCR_EL2.IMO)

  if (hvc_vm_start(PONG_VM) != 0) {
    hvc_puts_lit("vm_start failed\n");
    return 1;
  }

  struct ivc_mailbox* tx = &IVC_SHM->box[PONG_VM]; // to pong
  struct ivc_mailbox* rx = &IVC_SHM->box[0];       // echoes from pong

  for (uint64_t round = 1; round <= ROUNDS; ++round) {
    ivc_send(tx, round, round == ROUNDS ? FLAG_LAST : 0);
    if (hvc_ivc_doorbell(PONG_VM) != 0) {
      hvc_puts_lit("doorbell failed\n");
      return 1;
    }
    while (rx->seq < round) {
      hvc_yield(); // cooperative wait — wfi would stall the whole core
    }
    if (rx->payload[0] != round) {
      hvc_puts_lit("echo mismatch\n");
      return 1;
    }
  }

  if (g_irq_count == 0) {
    hvc_puts_lit("no doorbell vIRQ received\n"); // replies must arrive as vIRQs too
    return 1;
  }

  hvc_puts_lit("pingpong: 1000 rounds ok\n");
  return 0;
}
