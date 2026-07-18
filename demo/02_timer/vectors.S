// demo/02_timer/vectors.S — minimal EL1 vector table for the timer demo.
//
// Only the current-EL-SPx IRQ entry (+0x280) is populated: the demo
// runs at EL1h with vIRQs unmasked. Every other entry wedges in a WFE
// loop so a stray exception shows up as a hang instead of silent
// corruption.
//
// The IRQ handler acks/EOIs through ICC_IAR1_EL1 / ICC_EOIR1_EL1 —
// which HCR_EL2.IMO redirects to the hardware-virtualized ICV_* view —
// and bumps g_irq_count for main()'s WFI loop.

.macro VEC_HANG
    .align 7
1:  wfe
    b 1b
.endm

.section .text.vectors, "ax"
    .align 11                   // 2^11 = 2048-byte alignment for VBAR_EL1
.global _demo_vectors
_demo_vectors:
    // --- Current EL with SP_EL0 ---
    VEC_HANG                    // +0x000 Synchronous
    VEC_HANG                    // +0x080 IRQ
    VEC_HANG                    // +0x100 FIQ
    VEC_HANG                    // +0x180 SError

    // --- Current EL with SP_ELx (EL1h — the demo's mode) ---
    VEC_HANG                    // +0x200 Synchronous
    .align 7                    // +0x280 IRQ ← injected vINTID lands here
    b _demo_irq_handler

    VEC_HANG                    // +0x300 FIQ
    VEC_HANG                    // +0x380 SError

    // --- Lower EL (AArch64) — no EL0 in this demo ---
    VEC_HANG                    // +0x400 Synchronous
    VEC_HANG                    // +0x480 IRQ
    VEC_HANG                    // +0x500 FIQ
    VEC_HANG                    // +0x580 SError

    // --- Lower EL (AArch32) ---
    VEC_HANG                    // +0x600 Synchronous
    VEC_HANG                    // +0x680 IRQ
    VEC_HANG                    // +0x700 FIQ
    VEC_HANG                    // +0x780 SError

_demo_irq_handler:
    stp     x0, x1, [sp, #-16]!

    mrs     x0, S3_0_C12_C12_0  // ICC_IAR1_EL1 (ICV view): ack, x0 = vINTID
    msr     S3_0_C12_C12_1, x0  // ICC_EOIR1_EL1: priority drop + deactivate

    adrp    x1, g_irq_count
    add     x1, x1, :lo12:g_irq_count
    ldr     x0, [x1]
    add     x0, x0, #1
    str     x0, [x1]

    ldp     x0, x1, [sp], #16
    eret
