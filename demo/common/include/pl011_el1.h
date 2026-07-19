// Guest-side PL011 driver for the emulated vuart.
//
// The frame sits at the QEMU virt physical UART address but is NOT
// mapped in Stage 2 — every access traps and is emulated by the
// hypervisor's vuart. This is the standard-driver path an unmodified
// OS takes: poll FR for TX, take the UART SPI for RX.

#ifndef NOVAVISOR_DEMO_PL011_EL1_H
#define NOVAVISOR_DEMO_PL011_EL1_H

#include "nova/abi/guest_layout.h"

#include <stdint.h>

#define PL011_BASE ((unsigned long)NOVA_VUART_IPA_BASE)

// Register offsets and bits (Arm DDI 0183).
#define PL011_DR      0x000
#define PL011_FR      0x018
#define PL011_IMSC    0x038
#define PL011_MIS     0x040
#define PL011_ICR     0x044
#define PL011_FR_RXFE (1U << 4)
#define PL011_FR_TXFF (1U << 5)
#define PL011_INT_RX  (1U << 4)

static inline volatile uint32_t* pl011_reg(unsigned long off) {
  return (volatile uint32_t*)(PL011_BASE + off);
}

static inline void pl011_putc(char c) {
  while ((*pl011_reg(PL011_FR) & PL011_FR_TXFF) != 0U) {
  }
  *pl011_reg(PL011_DR) = (uint32_t)(unsigned char)c;
}

static inline void pl011_puts(const char* s) {
  while (*s != '\0') {
    pl011_putc(*s++);
  }
}

// One RX byte, or -1 when the FIFO is empty.
static inline int pl011_try_getc(void) {
  if ((*pl011_reg(PL011_FR) & PL011_FR_RXFE) != 0U) {
    return -1;
  }
  return (int)(*pl011_reg(PL011_DR) & 0xFFU);
}

// Unmask the RX interrupt at the UART. GIC-side SPI enablement
// (gicd_enable_spi) is separate, like real hardware.
static inline void pl011_rx_irq_enable(void) {
  *pl011_reg(PL011_IMSC) = PL011_INT_RX;
}

#endif // NOVAVISOR_DEMO_PL011_EL1_H
