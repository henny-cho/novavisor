// Phase 10 demo, guest B: FP with the MMU left OFF.
//
// Computes the alternating harmonic series for ln 2 — 4M sequential
// double iterations, double guest A's work so it deterministically
// finishes second. Its accumulator shares the physical FP register
// file with A's under the 10 ms slice; an exact result proves the lazy
// switch kept them apart. Staying flat-mapped while A runs translated
// also proves the Stage 1 regime (SCTLR/TCR/TTBR...) is banked per
// guest — a leak of A's regime would send this guest through A's page
// tables, which don't exist in B's memory.

#include "demo_hvc.h"

#include <stdint.h>

#define ITERATIONS 4000000u
#define EXPECTED   693147055ull // (uint64_t)(ln 2 partial sum * 1e9)

static void put_u64(uint64_t v) {
  char buf[20];
  int  i = 0;
  do {
    buf[i++] = (char)('0' + (v % 10));
    v /= 10;
  } while (v != 0);
  while (i > 0) {
    hvc_putc(buf[--i]);
  }
}

int main(void) {
  double acc  = 0.0;
  double sign = 1.0;
  for (uint32_t k = 1; k <= ITERATIONS; ++k) {
    acc += sign / (double)k;
    sign = -sign;
  }

  const uint64_t scaled = (uint64_t)(acc * 1e9);
  if (scaled != EXPECTED) {
    hvc_puts_lit("fp: B bad ");
    put_u64(scaled);
    hvc_putc('\n');
    return 1;
  }
  hvc_puts_lit("fp: B ok\n");
  return 0;
}
