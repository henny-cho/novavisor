// Phase 10 demo, guest A: Stage 1 MMU + FP under time-sharing.
//
// Boots with the MMU off, builds its own page tables and turns Stage 1
// on (the alias read only works if translation is live), then starts
// its sibling and computes the Leibniz series for pi/4 in doubles —
// 2M sequential iterations whose IEEE result is bit-exact and known.
// The sibling (MMU off, different series, double the work) runs
// concurrently under the 10 ms slice, so the FP register file bounces
// between the two guests dozens of times mid-accumulation; both
// results coming out exact proves the lazy FP switch leaks nothing.

#include "demo_hvc.h"
#include "el1_mmu.h"

#include <stdint.h>

#define ITERATIONS 2000000u
#define EXPECTED   785398038ull // (uint64_t)(pi/4 partial sum * 1e9)

static uint64_t          l1_table[512] __attribute__((aligned(4096)));
static volatile uint64_t marker = 0x4D4D5530AA55AA55ull;

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
  el1_mmu_enable(l1_table);

  // Read our own .data through the alias GB: VA != IPA, so a match is
  // proof the walk went through our tables (and kept executing here).
  const volatile uint64_t* alias = (const volatile uint64_t*)((uintptr_t)&marker + EL1_MMU_ALIAS_OFFSET);
  if (*alias != marker) {
    hvc_puts_lit("mmu: alias mismatch\n");
    return 1;
  }
  hvc_puts_lit("mmu: on vaddr ok\n");

  hvc_vm_start(1);

  double acc  = 0.0;
  double sign = 1.0;
  for (uint32_t k = 0; k < ITERATIONS; ++k) {
    acc += sign / (2.0 * (double)k + 1.0);
    sign = -sign;
  }

  const uint64_t scaled = (uint64_t)(acc * 1e9);
  if (scaled != EXPECTED) {
    hvc_puts_lit("fp: A bad ");
    put_u64(scaled);
    hvc_putc('\n');
    return 1;
  }
  hvc_puts_lit("fp: A ok\n");
  return 0;
}
