// Phase 12 demo, initiator (slot 0 — physical core 0).
//
// True parallel round-trips: the responder lives in slot 2, whose
// affinity is physical core 1, so both sides busy-poll their SPSC ring
// ends simultaneously — no traps, yields, or doorbells inside a round.
// The measured RTT is real cross-core latency: ring 0 carries the
// sequence number to pong, ring 1 brings the echo back, and each round
// is timed with the virtual counter on this core.
//
// Protocol sentinels ride the same rings, outside the 1..ROUNDS range:
// pong announces MSG_READY once booted (keeps its core-1 boot out of
// the statistics), ping ends with MSG_DONE and waits for pong's DONE
// echo — which orders pong's farewell line strictly before our exit.

#include "demo_hvc.h"
#include "guest_ring.h"

#include <stdint.h>

#define ROUNDS  1000
#define PONG_VM 2 /* guest_table slot with core 1 affinity */

#define MSG_READY (~(uint64_t)0)
#define MSG_DONE  (~(uint64_t)1)

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

static void print_dec(uint64_t v) {
  char buf[20];
  int  i = sizeof(buf);
  do {
    buf[--i] = (char)('0' + v % 10);
    v /= 10;
  } while (v != 0);
  hvc_puts(&buf[i], sizeof(buf) - (size_t)i);
}

static void spin_ms(uint64_t ms) {
  const uint64_t until = read_cntvct() + read_cntfrq() * ms / 1000;
  while (read_cntvct() < until) {
    // burn virtual time
  }
}

static uint64_t pop_wait(uintptr_t ring) {
  uint64_t v;
  while (!ring_pop(ring, &v)) {
    // responder is on its own core — just poll
  }
  return v;
}

static void push_wait(uintptr_t ring, uint64_t v) {
  while (!ring_push(ring, v)) {
    // full only while the responder lags — poll
  }
}

int main(void) {
  hvc_puts_lit("ping: up\n");

  if (hvc_vm_start(PONG_VM) != 0) {
    hvc_puts_lit("vm_start failed\n");
    return 1;
  }
  if (pop_wait(ring1_base()) != MSG_READY) {
    hvc_puts_lit("bad ready marker\n");
    return 1;
  }

  uint64_t total_ticks = 0;
  for (uint64_t round = 1; round <= ROUNDS; ++round) {
    const uint64_t t0 = read_cntvct();
    push_wait(ring0_base(), round);
    const uint64_t echo = pop_wait(ring1_base());
    total_ticks += read_cntvct() - t0;
    if (echo != round) {
      hvc_puts_lit("echo mismatch\n");
      return 1;
    }
  }

  const uint64_t avg_ns = total_ticks * 1000000000ULL / read_cntfrq() / ROUNDS;
  hvc_puts_lit("smp pingpong: 1000 rounds, avg RTT=");
  print_dec(avg_ns);
  hvc_puts_lit(" ns\n");

  push_wait(ring0_base(), MSG_DONE);
  if (pop_wait(ring1_base()) != MSG_DONE) {
    hvc_puts_lit("bad done echo\n");
    return 1;
  }
  // pong powers off right after its DONE echo; give its farewell and
  // the hypervisor's system_off line 50 ms of wall time so this VM's
  // exit output lands strictly after them (single writer per instant).
  spin_ms(50);
  return 0;
}
