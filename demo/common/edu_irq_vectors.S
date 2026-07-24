// Shared EL1 vector table for EDU interrupt demos.
.macro VEC_HANG
    .align 7
1:  wfe
    b 1b
.endm

.section .text.vectors, "ax"
    .align 11
.global _demo_vectors
_demo_vectors:
    VEC_HANG
    VEC_HANG
    VEC_HANG
    VEC_HANG

    VEC_HANG
    .align 7
    b _demo_irq_handler
    VEC_HANG
    VEC_HANG

    VEC_HANG
    VEC_HANG
    VEC_HANG
    VEC_HANG

    VEC_HANG
    VEC_HANG
    VEC_HANG
    VEC_HANG

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

    mrs     x0, S3_0_C12_C12_0
    str     x0, [sp, #160]
    bl      demo_irq
    ldr     x0, [sp, #160]
    msr     S3_0_C12_C12_1, x0
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
