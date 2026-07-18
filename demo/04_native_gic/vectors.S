// demo/04_native_gic/vectors.S — EL1 vector table for the native GIC demo.
//
// Only the current-EL-SPx IRQ entry (+0x280) is populated. The handler
// performs the full architectural sequence for a periodic virtual
// timer: ack through ICC_IAR1_EL1 (the hardware-virtualized ICV view),
// bump g_tick, re-arm CNTV — writing CTL=ENABLE also clears the IMASK
// the hypervisor set when forwarding this level-triggered PPI — then
// priority-drop/deactivate through ICC_EOIR1_EL1.

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
    .align 7                    // +0x280 IRQ ← vINTID 27 lands here
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
    stp     x0, x1, [sp, #-32]!
    str     x2, [sp, #16]

    mrs     x0, S3_0_C12_C12_0  // ICC_IAR1_EL1 (ICV view): ack, x0 = vINTID

    adrp    x1, g_tick
    add     x1, x1, :lo12:g_tick
    ldr     x2, [x1]
    add     x2, x2, #1
    str     x2, [x1]

    mrs     x1, cntfrq_el0      // re-arm the next ~8 ms period
    lsr     x1, x1, #7
    msr     cntv_tval_el0, x1
    mov     x1, #1              // ENABLE; IMASK clear (unmask)
    msr     cntv_ctl_el0, x1

    msr     S3_0_C12_C12_1, x0  // ICC_EOIR1_EL1: priority drop + deactivate

    ldr     x2, [sp, #16]
    ldp     x0, x1, [sp], #32
    eret
