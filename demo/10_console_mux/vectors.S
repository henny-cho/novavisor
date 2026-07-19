// demo/10_console_mux/vectors.S — EL1 vector table for both guests.
//
// Only the current-EL-SPx IRQ entry (+0x280) is populated. The handler
// acks through ICC_IAR1_EL1 (hardware-virtualized ICV view), hands the
// vINTID to the C handler (RX drain — demo_irq in console_demo.h),
// then priority-drops through ICC_EOIR1_EL1. Caller-saved registers
// are preserved around the C call; the demo builds with
// -mgeneral-regs-only, so no FP state is at risk.

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
    .align 7                    // +0x280 IRQ ← the SGIs land here
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
    sub     sp, sp, #176
    stp     x0, x1,   [sp, #0]
    stp     x2, x3,   [sp, #16]
    stp     x4, x5,   [sp, #32]
    stp     x6, x7,   [sp, #48]
    stp     x8, x9,   [sp, #64]
    stp     x10, x11, [sp, #80]
    stp     x12, x13, [sp, #96]
    stp     x14, x15, [sp, #112]
    stp     x16, x17, [sp, #128]
    stp     x18, x30, [sp, #144]

    mrs     x0, S3_0_C12_C12_0  // ICC_IAR1_EL1 (ICV view): ack, x0 = vINTID
    str     x0, [sp, #160]      // keep the INTID across the C call
    bl      demo_irq
    ldr     x0, [sp, #160]
    msr     S3_0_C12_C12_1, x0  // ICC_EOIR1_EL1: priority drop + deactivate
    isb

    ldp     x0, x1,   [sp, #0]
    ldp     x2, x3,   [sp, #16]
    ldp     x4, x5,   [sp, #32]
    ldp     x6, x7,   [sp, #48]
    ldp     x8, x9,   [sp, #64]
    ldp     x10, x11, [sp, #80]
    ldp     x12, x13, [sp, #96]
    ldp     x14, x15, [sp, #112]
    ldp     x16, x17, [sp, #128]
    ldp     x18, x30, [sp, #144]
    add     sp, sp, #176
    eret
