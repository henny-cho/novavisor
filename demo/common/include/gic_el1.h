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

#include "nova/abi/guest_layout.h"
#include "nova/arch/gicv3_regs.h"

#include <stdint.h>

// Frame bases from the guest-platform contract, register offsets from
// the shared architecture header — the same definitions the vGIC
// emulation is built on.
#define GICD_BASE ((unsigned long)NOVA_GICD_IPA_BASE)
#define GICR_BASE ((unsigned long)NOVA_GICR_IPA_BASE)

static inline volatile uint32_t* gic_reg32(unsigned long addr) {
  return (volatile uint32_t*)addr;
}

// Enable Group 1 forwarding at the distributor (affinity routing on).
static inline void gicd_enable_group1(void) {
  *gic_reg32(GICD_BASE + NOVA_GICD_CTLR) = NOVA_GICD_CTLR_ARE | NOVA_GICD_CTLR_ENABLE_GRP1;
}

// A vCPU's redistributor frame base: frames are strided per vCPU
// (SMP guests), and each vCPU programs its own (index = MPIDR Aff0).
static inline unsigned long gicr_frame(unsigned vcpu) {
  return GICR_BASE + (unsigned long)vcpu * NOVA_GICR_FRAME_SIZE;
}

// This vCPU's index within the VM (virtual MPIDR Aff0).
static inline unsigned my_vcpu(void) {
  uint64_t mpidr;
  __asm__ volatile("mrs %0, mpidr_el1" : "=r"(mpidr));
  return (unsigned)(mpidr & 0xFFU);
}

// Redistributor wake handshake on one frame: clear ProcessorSleep,
// wait for ChildrenAsleep to drop.
static inline void gicr_wake_at(unsigned vcpu) {
  volatile uint32_t* waker = gic_reg32(gicr_frame(vcpu) + NOVA_GICR_WAKER);
  *waker                   = *waker & ~NOVA_GICR_WAKER_PROCESSOR_SLEEP;
  while ((*waker & NOVA_GICR_WAKER_CHILDREN_ASLEEP) != 0U) {
  }
}

static inline void gicr_wake(void) {
  gicr_wake_at(0);
}

// Put one SGI/PPI in Group 1 at a mid priority and enable it, on one
// vCPU's frame.
static inline void gicr_enable_at(unsigned vcpu, unsigned intid) {
  const unsigned long base                                = gicr_frame(vcpu);
  volatile uint32_t*  igroupr0                            = gic_reg32(base + NOVA_GICR_IGROUPR0);
  *igroupr0                                               = *igroupr0 | (1U << intid);
  *gic_reg32(base + NOVA_GICR_IPRIORITYR + (intid & ~3U)) = 0x80808080U;
  *gic_reg32(base + NOVA_GICR_ISENABLER0)                 = 1U << intid; // write-1-to-set
}

static inline void gicr_enable(unsigned intid) {
  gicr_enable_at(0, intid);
}

// Enable one SPI (INTID 32..63) at the distributor. Group and route
// keep their reset values (Group 1, vCPU 0); a mid priority is set so
// the ICV priority mask never filters it.
static inline void gicd_enable_spi(unsigned intid) {
  *gic_reg32(GICD_BASE + NOVA_GICD_IPRIORITYR + (intid & ~3U)) = 0x80808080U;
  *gic_reg32(GICD_BASE + NOVA_GICD_ISENABLER1)                 = 1U << (intid % 32U); // write-1-to-set
}

// Generate a Group 1 SGI toward sibling vCPUs (one TargetList bit per
// vCPU index). The write is trapped (ICH_HCR.TC) and routed by the
// hypervisor — this is the guest's cross-vCPU IPI.
static inline void icc_send_sgi(uint32_t target_list, uint32_t intid) {
  const uint64_t v = ((uint64_t)(intid & 0xFU) << 24) | (target_list & 0xFFFFU);
  __asm__ volatile("dsb ishst\n\tmsr S3_0_C12_C11_5, %0\n\tisb" ::"r"(v)); // ICC_SGI1R_EL1
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
