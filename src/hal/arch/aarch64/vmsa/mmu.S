// hal/arch/aarch64/vmsa/mmu.S
//
// MMU primitives: EL2's own Stage-1 regime (called from boot paths)
// and Stage 2 (called from components/core_mmu/src/core_mmu.cpp).
//
// Contract:
//   void nova_el2_stage1_enable(uint64_t ttbr0, uint64_t tcr,
//                               uint64_t mair, uint64_t sctlr);
//       Loads the EL2 Stage-1 translation registers, discards stale
//       EL2 TLB and I-cache contents, then writes SCTLR_EL2 as a
//       whole (M+C+I+WXN — never a read-modify of firmware state).
//       Leaf and stack-free: the secondary entry calls it before the
//       core has a stack, so only x0-x3 and lr may be used.
//
//   void nova_dcache_inv_all(void);
//       Invalidate (no writeback) every data/unified cache level to
//       the point of coherency, by set/way. Cold-boot only, before
//       BSS zeroing: discards whatever firmware left in the hierarchy
//       so nothing stale can evict over EL2's pre-MMU RAM writes.
//       Leaf and stack-free; clobbers x0-x10.
//
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
//     No ISB either: VTTBR affects only EL1&0 translation, which EL2
//     code never uses, and the ERET back into the guest is itself
//     context-synchronizing (same contract as VMPIDR/CNTVOFF on the
//     switch path).
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

.global nova_el2_stage1_enable
.type   nova_el2_stage1_enable, @function
.section .text.nova_el2_stage1_enable

// Enable ordering:
//   1. DSB SY  — the just-built tables are observable before the
//                walker can consume them.
//   2. msr MAIR/TCR/TTBR0 + ISB — translation config in place.
//   3. TLBI ALLE2 — drop stale EL2 entries (firmware leftovers).
//   4. IC IALLU — drop stale instruction lines before I=1.
//   5. msr SCTLR + ISB — M/C/I/WXN take effect together.
nova_el2_stage1_enable:
    dsb     sy
    msr     mair_el2,  x2
    msr     tcr_el2,   x1
    msr     ttbr0_el2, x0
    isb

    tlbi    alle2
    dsb     nsh
    ic      iallu
    dsb     nsh
    isb

    msr     sctlr_el2, x3
    isb
    ret
.size nova_el2_stage1_enable, . - nova_el2_stage1_enable

.global nova_dcache_inv_all
.type   nova_dcache_inv_all, @function
.section .text.nova_dcache_inv_all

// Classic CLIDR/CCSIDR set/way walk (ARM ARM K11 example). DC ISW
// (invalidate, no clean): at this point EL2 has written nothing worth
// keeping, and a clean could push firmware's stale lines into RAM.
nova_dcache_inv_all:
    dsb     sy
    mrs     x0, clidr_el1
    ubfx    x3, x0, #24, #3         // LoC
    cbz     x3, 4f
    lsl     x3, x3, #1              // loop bound: LoC << 1
    mov     x10, xzr                // cache level << 1
1:
    add     x2, x10, x10, lsr #1    // level * 3: CLIDR ctype offset
    lsr     x1, x0, x2
    and     x1, x1, #7
    cmp     x1, #2                  // data or unified cache here?
    b.lt    3f
    msr     csselr_el1, x10
    isb
    mrs     x1, ccsidr_el1
    ubfx    x2, x1, #0, #3
    add     x2, x2, #4              // log2(line bytes): set shift
    ubfx    x4, x1, #3, #10         // ways - 1
    clz     w5, w4                  // way shift
    ubfx    x6, x1, #13, #15        // sets - 1
2:
    mov     x9, x4                  // way counter
5:
    lsl     x7, x9, x5
    orr     x7, x10, x7             // way | level
    lsl     x8, x6, x2
    orr     x7, x7, x8              // | set
    dc      isw, x7
    subs    x9, x9, #1
    b.ge    5b
    subs    x6, x6, #1
    b.ge    2b
3:
    add     x10, x10, #2
    cmp     x3, x10
    b.gt    1b
4:
    msr     csselr_el1, xzr
    dsb     sy
    isb
    ret
.size nova_dcache_inv_all, . - nova_dcache_inv_all

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
    ret
.size nova_stage2_switch, . - nova_stage2_switch
