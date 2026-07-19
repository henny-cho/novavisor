// Shared logic for both console-mux guests. Each VM talks to its own
// emulated PL011 with standard driver code: banner and tick lines go
// out through DR polling (the hypervisor tags them per VM), then one
// host line is received over the RX interrupt path — UART SPI enabled
// at the distributor, IMSC unmasked at the device — and echoed back.
//
// The wait uses the mask-check-wfi pattern: a one-shot wake arriving
// between the flag check and the wfi must not be lost.

#ifndef NOVAVISOR_DEMO_CONSOLE_DEMO_H
#define NOVAVISOR_DEMO_CONSOLE_DEMO_H

#include "demo_hvc.h"
#include "gic_el1.h"
#include "nova/abi/guest_layout.h"
#include "pl011_el1.h"

#include <stdint.h>

extern char _demo_vectors[];

static volatile int g_line_done;
static char         g_line[64];
static unsigned     g_len;

// RX interrupt: drain the FIFO completely (the level re-arms per
// FIFO-empty edge, so leftovers would wait for the next byte).
void demo_irq(uint64_t intid) {
  if (intid != NOVA_VUART_SPI) {
    return;
  }
  for (;;) {
    const int c = pl011_try_getc();
    if (c < 0) {
      break;
    }
    if (c == '\n' || c == '\r') {
      g_line[g_len] = '\0';
      g_line_done   = 1;
    } else if (g_len < sizeof(g_line) - 1) {
      g_line[g_len++] = (char)c;
    }
  }
}

static inline void irq_unmask(void) {
  __asm__ volatile("msr daifclr, #2");
}

static inline void irq_mask(void) {
  __asm__ volatile("msr daifset, #2");
}

// peer_vm >= 0: start that VM right after the banner — the banner
// order in the output stays deterministic for the harness.
static int run_console_demo(int peer_vm) {
  __asm__ volatile("msr vbar_el1, %0" ::"r"(_demo_vectors));
  gicd_enable_group1();
  gicr_wake();
  icc_init();
  gicd_enable_spi(NOVA_VUART_SPI);
  pl011_rx_irq_enable();
  irq_unmask();

  pl011_puts("vuart up\n");
  if (peer_vm >= 0) {
    (void)hvc_vm_start((uint64_t)peer_vm);
  }

  for (unsigned i = 0; i < 5; ++i) {
    pl011_puts("tick ");
    pl011_putc((char)('0' + i));
    pl011_putc('\n');
    for (volatile unsigned d = 0; d < 3000000U; ++d) {
      // spread the ticks past the peer's boot latency so both cores'
      // lines visibly interleave
    }
  }

  while (!g_line_done) {
    irq_mask();
    if (!g_line_done) {
      __asm__ volatile("wfi");
    }
    irq_unmask();
  }

  pl011_puts("echo: ");
  pl011_puts(g_line);
  pl011_putc('\n');
  return 0;
}

#endif // NOVAVISOR_DEMO_CONSOLE_DEMO_H
