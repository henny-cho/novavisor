// Phase 12 demo, responder (slot 2 — physical core 1).
//
// Pure echo loop on the other physical core: pop the sequence number
// from ring 0, push it straight back on ring 1. Announces itself with
// MSG_READY (so the initiator's statistics exclude this VM's boot),
// answers MSG_DONE with the same marker — ordering the farewell line
// before the initiator's final fault — and retires through PSCI
// SYSTEM_OFF after surviving the initiator's three warm resets.

#include "demo_hvc.h"
#include "guest_psci.h"
#include "guest_ring.h"

#include <stdint.h>

#define MSG_READY (~(uint64_t)0)
#define MSG_DONE  (~(uint64_t)1)

int main(void) {
  hvc_puts_lit("pong: ready\n");
  while (!ring_push(ring1_base(), MSG_READY)) {
  }

  for (;;) {
    uint64_t v;
    while (!ring_pop(ring0_base(), &v)) {
      // initiator is on its own core — just poll
    }
    if (v == MSG_DONE) {
      break;
    }
    while (!ring_push(ring1_base(), v)) {
    }
  }

  hvc_puts_lit("pong: done\n");
  while (!ring_push(ring1_base(), MSG_DONE)) {
  }
  psci_system_off();
}
