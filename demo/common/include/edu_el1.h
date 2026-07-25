#ifndef DEMO_EDU_EL1_H
#define DEMO_EDU_EL1_H

/* EL1 guest driver helpers for the passed-through edu device. The
 * register contract (offsets, identity, DMA bits) and the BAR window
 * come from the shared ABI headers; only the guest-side accessors live
 * here, next to the other EL1 device helpers. */

#include "nova/abi/edu.h"
#include "nova/abi/guest_layout.h"

#include <stdint.h>

static inline volatile uint32_t* nova_edu_reg32(uint32_t offset) {
  return (volatile uint32_t*)((uintptr_t)NOVA_EDU_BAR0_IPA + offset);
}

static inline volatile uint64_t* nova_edu_reg64(uint32_t offset) {
  return (volatile uint64_t*)((uintptr_t)NOVA_EDU_BAR0_IPA + offset);
}

static inline uint32_t nova_edu_read32(uint32_t offset) {
  return *nova_edu_reg32(offset);
}

static inline void nova_edu_write32(uint32_t offset, uint32_t value) {
  *nova_edu_reg32(offset) = value;
}

static inline void nova_edu_write64(uint32_t offset, uint64_t value) {
  *nova_edu_reg64(offset) = value;
}

static inline void nova_edu_publish(void) {
  __asm__ volatile("dsb oshst" ::: "memory");
}

static inline void nova_edu_acquire(void) {
  __asm__ volatile("dsb osh" ::: "memory");
}

static inline void nova_edu_submit_dma(uint64_t source, uint64_t destination, uint64_t count, uint64_t flags) {
  nova_edu_write64(NOVA_EDU_DMA_SOURCE, source);
  nova_edu_write64(NOVA_EDU_DMA_DEST, destination);
  nova_edu_write64(NOVA_EDU_DMA_COUNT, count);
  nova_edu_write64(NOVA_EDU_DMA_COMMAND, NOVA_EDU_DMA_RUN | (flags & (NOVA_EDU_DMA_TO_PCI | NOVA_EDU_DMA_IRQ)));
  nova_edu_publish();
}

#endif /* DEMO_EDU_EL1_H */
