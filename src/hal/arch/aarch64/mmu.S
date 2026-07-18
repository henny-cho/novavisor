// hal/arch/aarch64/mmu.S
//
// Stage 2 MMU primitives, called from components/core_mmu/src/core_mmu.cpp.
//
// Contract:
//   void nova_stage2_activate(uint64_t vttbr, uint64_t vtcr, uint64_t hcr);
//       x0 = VTTBR_EL2 value (L1 table PA | VMID<<48)
//       x1 = VTCR_EL2 value
//       x2 = HCR_EL2 value (must already have VM=1)
//     One-time boot activation, after ALL per-guest tables are populated.
//
//   void nova_stage2_switch(uint64_t vttbr);
//       x0 = VTTBR_EL2 value of the target guest
//     VM switch on the trap path. No TLB maintenance: entries are
//     VMID-tagged and the static tables never change after build.
//
// Activation barrier ordering (ARM ARM D5.10.1 "TLB maintenance
// requirements"):
//   1. DSB SY  — ensure all prior stores (the Stage 2 tables themselves)
//                are observable before the MMU consumes them.
//   2. msr VTTBR, VTCR — set translation base + control.
//   3. ISB     — ensure MMU sees the new config before we touch HCR.
//   4. msr HCR — flip VM=1 to make Stage 2 take effect.
//   5. ISB     — ensure subsequent instruction fetches honor new HCR.
//   6. TLBI ALLE1IS — invalidate stale Stage 1+2 EL1 entries for ALL
//                     VMIDs (every guest's tables were just written).
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

    tlbi    alle1is
    dsb     sy
    isb

    ret
.size nova_stage2_activate, . - nova_stage2_activate

.global nova_stage2_switch
.type   nova_stage2_switch, @function
.section .text.nova_stage2_switch

nova_stage2_switch:
    msr     vttbr_el2, x0
    isb
    ret
.size nova_stage2_switch, . - nova_stage2_switch
