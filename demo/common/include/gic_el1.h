// Guest-side GICv3 programming helpers.
//
// The GICD/GICR frames sit at the QEMU virt physical addresses but are
// NOT mapped in Stage 2: every access below traps to the hypervisor and
// is emulated by its vGIC. This is the architecture-standard path an
// unmodified OS takes — SGIs are enabled at reset, but a guest that
// wants a PPI (e.g. the virtual timer, INTID 27) must enable it here.
//
// The CPU interface is NOT programmed through MMIO: EL1 ICC_* system
// register accesses are hardware-virtualized into the ICV_* view.

#ifndef NOVAVISOR_DEMO_GIC_EL1_H
#define NOVAVISOR_DEMO_GIC_EL1_H

#include <stdint.h>

#define GICD_BASE 0x08000000UL
#define GICR_BASE 0x080A0000UL

#define GICD_CTLR             (GICD_BASE + 0x0000)
#define GICD_CTLR_ARE         (1U << 4)
#define GICD_CTLR_ENABLE_GRP1 (1U << 1)

#define GICR_WAKER                 (GICR_BASE + 0x0014)
#define GICR_WAKER_PROCESSOR_SLEEP (1U << 1)
#define GICR_WAKER_CHILDREN_ASLEEP (1U << 2)

#define GICR_SGI_FRAME  (GICR_BASE + 0x10000)
#define GICR_IGROUPR0   (GICR_SGI_FRAME + 0x0080)
#define GICR_ISENABLER0 (GICR_SGI_FRAME + 0x0100)
#define GICR_IPRIORITYR (GICR_SGI_FRAME + 0x0400)

static inline volatile uint32_t* gic_reg32(unsigned long addr) {
  return (volatile uint32_t*)addr;
}

// Enable Group 1 forwarding at the distributor (affinity routing on).
static inline void gicd_enable_group1(void) {
  *gic_reg32(GICD_CTLR) = GICD_CTLR_ARE | GICD_CTLR_ENABLE_GRP1;
}

// Redistributor wake handshake: clear ProcessorSleep, wait for
// ChildrenAsleep to drop.
static inline void gicr_wake(void) {
  volatile uint32_t* waker = gic_reg32(GICR_WAKER);
  *waker                   = *waker & ~GICR_WAKER_PROCESSOR_SLEEP;
  while ((*waker & GICR_WAKER_CHILDREN_ASLEEP) != 0U) {
  }
}

// Put one SGI/PPI in Group 1 at a mid priority and enable it.
static inline void gicr_enable(unsigned intid) {
  *gic_reg32(GICR_IGROUPR0)                   = *gic_reg32(GICR_IGROUPR0) | (1U << intid);
  *gic_reg32(GICR_IPRIORITYR + (intid & ~3U)) = 0x80808080U;
  *gic_reg32(GICR_ISENABLER0)                 = 1U << intid; // write-1-to-set
}

// CPU interface (ICV view): accept all priorities, enable Group 1.
static inline void icc_init(void) {
  uint64_t v = 0xFF;
  __asm__ volatile("msr S3_0_C4_C6_0, %0" ::"r"(v)); // ICC_PMR_EL1
  v = 1;
  __asm__ volatile("msr S3_0_C12_C12_7, %0" ::"r"(v)); // ICC_IGRPEN1_EL1
  __asm__ volatile("isb");
}

#endif // NOVAVISOR_DEMO_GIC_EL1_H
