#include "demo_hvc.h"
#include "gic_el1.h"
#include "nova/abi/edu.h"

#include <stdint.h>

extern char _demo_vectors[];

static volatile uint32_t g_irq_count                            = 0;
static volatile uint32_t g_unexpected_irq                       = 0;
static volatile uint32_t g_bad_irq_status                       = 0;
static volatile uint64_t g_source __attribute__((aligned(64)))  = 0x4E4F5641444D4131ULL;
static volatile uint64_t g_scratch __attribute__((aligned(64))) = 0;

static inline void irq_mask(void) {
  __asm__ volatile("msr daifset, #2");
}

static inline void irq_unmask(void) {
  __asm__ volatile("msr daifclr, #2");
}

static inline void cache_clean(const volatile void* address) {
  __asm__ volatile("dc cvac, %0\ndsb osh" ::"r"(address) : "memory");
}

static inline void cache_invalidate(const volatile void* address) {
  __asm__ volatile("dc ivac, %0\ndsb osh" ::"r"(address) : "memory");
}

void demo_irq(uint32_t intid) {
  if (intid != NOVA_EDU_SPI) {
    g_unexpected_irq = intid;
    return;
  }

  const uint32_t pending = nova_edu_read32(NOVA_EDU_IRQ_STATUS);
  if (pending != NOVA_EDU_DMA_IRQ_STATUS) {
    g_bad_irq_status = pending == 0U ? UINT32_MAX : pending;
  }
  nova_edu_write32(NOVA_EDU_IRQ_ACK, pending);
  nova_edu_publish();
  g_irq_count = g_irq_count + 1U;
}

static int wait_for_dma(uint32_t expected) {
  while (g_irq_count != expected && g_unexpected_irq == 0U && g_bad_irq_status == 0U) {
    __asm__ volatile("wfi");
    irq_unmask();
    irq_mask();
  }
  irq_unmask();
  return g_irq_count == expected && g_unexpected_irq == 0U && g_bad_irq_status == 0U;
}

static int run_dma_round_trip(void) {
  const uint64_t source_iova  = (uint64_t)(uintptr_t)&g_source;
  const uint64_t scratch_iova = (uint64_t)(uintptr_t)&g_scratch;

  cache_clean(&g_source);
  cache_clean(&g_scratch);

  irq_mask();
  nova_edu_submit_dma(source_iova, NOVA_EDU_INTERNAL_BUFFER, sizeof(g_source), NOVA_EDU_DMA_IRQ);
  if (!wait_for_dma(1U)) {
    return 0;
  }

  irq_mask();
  nova_edu_submit_dma(NOVA_EDU_INTERNAL_BUFFER, scratch_iova, sizeof(g_scratch),
                      NOVA_EDU_DMA_TO_PCI | NOVA_EDU_DMA_IRQ);
  if (!wait_for_dma(2U)) {
    return 0;
  }

  cache_invalidate(&g_scratch);
  nova_edu_acquire();
  return g_scratch == g_source;
}

int main(void) {
  if (nova_edu_read32(NOVA_EDU_IDENTITY_REG) != NOVA_EDU_IDENTITY) {
    hvc_puts_lit("edu dma: identity mismatch\n");
    return 1;
  }

  __asm__ volatile("msr vbar_el1, %0" ::"r"(_demo_vectors));
  gicd_enable_group1();
  gicd_enable_spi(NOVA_EDU_SPI);
  icc_init();
  irq_unmask();

  if (!run_dma_round_trip()) {
    hvc_puts_lit("edu dma: round-trip failed\n");
    return 2;
  }

  hvc_puts_lit("edu dma: direct round-trip and 2 irqs ok\n");
  return 0;
}
