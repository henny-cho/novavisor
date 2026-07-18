// hal/arch/aarch64/fp.S
//
// FP/SIMD register bank save/restore — the ONLY code in the EL2 image
// permitted to touch the FP register file (scripts/check_fp_free.sh
// enforces this post-link). Callers clear CPTR_EL2.TFP + ISB first or
// these instructions self-trap.
//
// x0 = FpBank*. Offsets match nova::arch::FpBank: Q0..Q31 at [0, 512),
// FPSR at 512, FPCR at 520; the struct is 16-byte aligned so the
// quad-register pair accesses satisfy -mstrict-align.

.macro fp_bank op
    \op     q0,  q1,  [x0, #0]
    \op     q2,  q3,  [x0, #32]
    \op     q4,  q5,  [x0, #64]
    \op     q6,  q7,  [x0, #96]
    \op     q8,  q9,  [x0, #128]
    \op     q10, q11, [x0, #160]
    \op     q12, q13, [x0, #192]
    \op     q14, q15, [x0, #224]
    \op     q16, q17, [x0, #256]
    \op     q18, q19, [x0, #288]
    \op     q20, q21, [x0, #320]
    \op     q22, q23, [x0, #352]
    \op     q24, q25, [x0, #384]
    \op     q26, q27, [x0, #416]
    \op     q28, q29, [x0, #448]
    \op     q30, q31, [x0, #480]
.endm

.global nova_fp_save
.type   nova_fp_save, @function
.section .text.nova_fp_save
nova_fp_save:
    fp_bank stp
    mrs     x1, fpsr
    mrs     x2, fpcr
    str     x1, [x0, #512]      // stp's signed offset tops out at 504
    str     x2, [x0, #520]
    ret
.size nova_fp_save, . - nova_fp_save

.global nova_fp_restore
.type   nova_fp_restore, @function
.section .text.nova_fp_restore
nova_fp_restore:
    ldr     x1, [x0, #512]
    ldr     x2, [x0, #520]
    msr     fpsr, x1
    msr     fpcr, x2
    fp_bank ldp
    ret
.size nova_fp_restore, . - nova_fp_restore
