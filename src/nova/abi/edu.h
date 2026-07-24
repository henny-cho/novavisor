#ifndef NOVA_ABI_EDU_H
#define NOVA_ABI_EDU_H

#include "nova/abi/guest_layout.h"

#include <stdint.h>

// Shared by C guests and the C++ board backend.
// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)
#define NOVA_EDU_IDENTITY_REG 0x00U
#define NOVA_EDU_IRQ_STATUS   0x24U
#define NOVA_EDU_IRQ_RAISE    0x60U
#define NOVA_EDU_IRQ_ACK      0x64U
#define NOVA_EDU_DMA_SOURCE   0x80U
#define NOVA_EDU_DMA_DEST     0x88U
#define NOVA_EDU_DMA_COUNT    0x90U
#define NOVA_EDU_DMA_COMMAND  0x98U

#define NOVA_EDU_IDENTITY        0x010000EDU
#define NOVA_EDU_DMA_RUN         (1U << 0U)
#define NOVA_EDU_DMA_TO_PCI      (1U << 1U)
#define NOVA_EDU_DMA_IRQ         (1U << 2U)
#define NOVA_EDU_DMA_IRQ_STATUS  (1U << 8U)
#define NOVA_EDU_INTERNAL_BUFFER 0x00040000ULL
#define NOVA_EDU_BUFFER_SIZE     4096U
// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#ifndef __cplusplus

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

#endif

#endif /* NOVA_ABI_EDU_H */
