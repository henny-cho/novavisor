#include "demo_hvc.h"
#include "gic_el1.h"
#include "nova/abi/edu.h"

#include <stdint.h>

#define IRQ_ROUNDS 8U

extern char _demo_vectors[];

static volatile uint32_t g_irq_count        = 0;
static volatile uint32_t g_unexpected_irq   = 0;
static volatile uint32_t g_empty_irq_status = 0;

static inline void irq_mask(void) {
  __asm__ volatile("msr daifset, #2");
}

static inline void irq_unmask(void) {
  __asm__ volatile("msr daifclr, #2");
}

void demo_irq(uint32_t intid) {
  if (intid != NOVA_EDU_SPI) {
    g_unexpected_irq = intid;
    return;
  }

  const uint32_t pending = nova_edu_read32(NOVA_EDU_IRQ_STATUS);
  if (pending == 0U) {
    g_empty_irq_status = 1;
    return;
  }
  nova_edu_write32(NOVA_EDU_IRQ_ACK, pending);
  nova_edu_publish();
  g_irq_count = g_irq_count + 1U;
}

static int run_irq_round(uint32_t expected) {
  irq_mask();
  nova_edu_write32(NOVA_EDU_IRQ_RAISE, 1U);
  nova_edu_publish();

  while (g_irq_count != expected && g_unexpected_irq == 0U && g_empty_irq_status == 0U) {
    __asm__ volatile("wfi");
    irq_unmask();
    irq_mask();
  }
  irq_unmask();
  return g_irq_count == expected && g_unexpected_irq == 0U && g_empty_irq_status == 0U;
}

int main(void) {
  if (nova_edu_read32(NOVA_EDU_IDENTITY_REG) != NOVA_EDU_IDENTITY) {
    hvc_puts_lit("edu passthrough: identity mismatch\n");
    return 1;
  }

  __asm__ volatile("msr vbar_el1, %0" ::"r"(_demo_vectors));
  gicd_enable_group1();
  gicd_enable_spi(NOVA_EDU_SPI);
  icc_init();
  irq_unmask();

  for (uint32_t round = 1; round <= IRQ_ROUNDS; ++round) {
    if (!run_irq_round(round)) {
      hvc_puts_lit("edu passthrough: interrupt mismatch\n");
      return 2;
    }
  }

  hvc_puts_lit("edu passthrough: 8 level irqs ok\n");
  return 0;
}
