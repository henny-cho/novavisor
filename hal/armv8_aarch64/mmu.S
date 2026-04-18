// hal/armv8_aarch64/mmu.S
//
// Stage 2 MMU activation primitive. Called once from
// components/core_mmu/src/core_mmu.cpp after the Stage 2 tables are
// populated.
//
// Contract:
//   void nova_stage2_activate(uint64_t vttbr, uint64_t vtcr, uint64_t hcr);
//       x0 = VTTBR_EL2 value (L1 table PA | VMID<<48)
//       x1 = VTCR_EL2 value
//       x2 = HCR_EL2 value (must already have VM=1)
//
// Barrier ordering (ARM ARM D5.10.1 "TLB maintenance requirements"):
//   1. DSB SY  — ensure all prior stores (the Stage 2 tables themselves)
//                are observable before the MMU consumes them.
//   2. msr VTTBR, VTCR — set translation base + control.
//   3. ISB     — ensure MMU sees the new config before we touch HCR.
//   4. msr HCR — flip VM=1 to make Stage 2 take effect.
//   5. ISB     — ensure subsequent instruction fetches honor new HCR.
//   6. TLBI VMALLS12E1IS — invalidate stale Stage 1+2 entries for this
//                          VMID across inner-shareable domain.
//   7. DSB SY + ISB — quiesce invalidations.

.global nova_stage2_activate
.type   nova_stage2_activate, @function
.section .text.nova_stage2_activate

nova_stage2_activate:
    dsb     sy
    msr     vttbr_el2, x0
    msr     vtcr_el2,  x1
    isb

    msr     hcr_el2,   x2
    isb

    tlbi    vmalls12e1is
    dsb     sy
    isb

    ret
.size nova_stage2_activate, . - nova_stage2_activate
