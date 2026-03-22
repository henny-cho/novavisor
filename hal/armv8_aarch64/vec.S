// vec.S — EL2 Exception Vector Table (AArch64, QEMU virt)
//
// Layout: VBAR_EL2 must be 2048-byte (2^11) aligned.
// Each of the 16 entries occupies exactly 128 bytes (2^7).
//
// Offset  Level/Type
// +0x000  EL2h SPh — Synchronous
// +0x080  EL2h SPh — IRQ
// +0x100  EL2h SPh — FIQ
// +0x180  EL2h SPh — SError
// +0x200  EL2h SP0 — Synchronous  ← EL2 self-trap (current EL)
// +0x280  EL2h SP0 — IRQ
// +0x300  EL2h SP0 — FIQ
// +0x380  EL2h SP0 — SError
// +0x400  Lower AArch64 — Synchronous  ← HVC / guest memory faults
// +0x480  Lower AArch64 — IRQ
// +0x500  Lower AArch64 — FIQ
// +0x580  Lower AArch64 — SError
// +0x600  Lower AArch32 — Synchronous
// +0x680  Lower AArch32 — IRQ
// +0x700  Lower AArch32 — FIQ
// +0x780  Lower AArch32 — SError
//
// TrapContext offsets (must match novavisor/trap_context.hpp):
//   0..240   x0–x30
//   248      SP_EL1
//   256      ELR_EL2
//   264      SPSR_EL2
//   272      ESR_EL2
//   280      FAR_EL2

.global _vector_table
.section .text.vec, "ax", @progbits

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

#define TRAP_CTX_SIZE  288
#define CTX_X30        240
#define CTX_SP         248
#define CTX_ELR        256
#define CTX_SPSR       264
#define CTX_ESR        272
#define CTX_FAR        280

// ---------------------------------------------------------------------------
// TRAP_ENTRY macro — exactly 128 bytes per entry
//
// Uses `b` (not `bl`) so that x30 (guest LR) is NOT clobbered here.
// The shared handler body is responsible for saving x30 via TRAP_SAVE_CONTEXT.
// ---------------------------------------------------------------------------

.macro TRAP_ENTRY handler
    .align 7                    // 128-byte slot boundary
    sub sp, sp, #TRAP_CTX_SIZE
    b \handler
    .align 7                    // pad to fill the full 128-byte slot
.endm

// ---------------------------------------------------------------------------
// TRAP_SAVE_CONTEXT macro — saves all guest registers into TrapContext on stack
//
// Called from shared handler bodies (outside the 128-byte entry slots).
// On entry: sp points to the TrapContext frame (already allocated by TRAP_ENTRY).
// On exit:  x0 = sp (pointer to TrapContext, ready to pass as first argument).
// Clobbers: x0–x3 (used as temporaries for system register reads).
// ---------------------------------------------------------------------------

.macro TRAP_SAVE_CONTEXT
    // Save x0–x29 in pairs (15 stp instructions)
    stp x0,  x1,  [sp, #0]
    stp x2,  x3,  [sp, #16]
    stp x4,  x5,  [sp, #32]
    stp x6,  x7,  [sp, #48]
    stp x8,  x9,  [sp, #64]
    stp x10, x11, [sp, #80]
    stp x12, x13, [sp, #96]
    stp x14, x15, [sp, #112]
    stp x16, x17, [sp, #128]
    stp x18, x19, [sp, #144]
    stp x20, x21, [sp, #160]
    stp x22, x23, [sp, #176]
    stp x24, x25, [sp, #192]
    stp x26, x27, [sp, #208]
    stp x28, x29, [sp, #224]

    // Save x30 (guest LR) and SP_EL1 at offset 240 and 248
    mrs x0, sp_el1
    stp x30, x0, [sp, #CTX_X30]

    // Save ELR_EL2, SPSR_EL2, ESR_EL2, FAR_EL2
    mrs x0, elr_el2
    mrs x1, spsr_el2
    stp x0, x1, [sp, #CTX_ELR]

    mrs x0, esr_el2
    mrs x1, far_el2
    stp x0, x1, [sp, #CTX_ESR]

    // Pass TrapContext* as first argument
    mov x0, sp
.endm

// ---------------------------------------------------------------------------
// Vector table — 16 entries × 128 bytes = 2048 bytes
// ---------------------------------------------------------------------------

    .align 11                   // 2^11 = 2048-byte alignment for VBAR_EL2

_vector_table:
    // --- EL2h / SPh (current EL with EL2 SP) ---
    TRAP_ENTRY _handle_el2h_sync      // +0x000 Synchronous
    TRAP_ENTRY _handle_unhandled      // +0x080 IRQ
    TRAP_ENTRY _handle_unhandled      // +0x100 FIQ
    TRAP_ENTRY _handle_unhandled      // +0x180 SError

    // --- EL2h / SP0 (current EL with SP0) ---
    TRAP_ENTRY _handle_el2h_sync      // +0x200 Synchronous
    TRAP_ENTRY _handle_unhandled      // +0x280 IRQ
    TRAP_ENTRY _handle_unhandled      // +0x300 FIQ
    TRAP_ENTRY _handle_unhandled      // +0x380 SError

    // --- Lower EL (AArch64) — primary guest trap path ---
    TRAP_ENTRY _handle_lower_sync     // +0x400 Synchronous ← HVC / data abort
    TRAP_ENTRY _handle_unhandled      // +0x480 IRQ
    TRAP_ENTRY _handle_unhandled      // +0x500 FIQ
    TRAP_ENTRY _handle_unhandled      // +0x580 SError

    // --- Lower EL (AArch32) --- (not used, but table must be complete)
    TRAP_ENTRY _handle_unhandled      // +0x600 Synchronous
    TRAP_ENTRY _handle_unhandled      // +0x680 IRQ
    TRAP_ENTRY _handle_unhandled      // +0x700 FIQ
    TRAP_ENTRY _handle_unhandled      // +0x780 SError

// ---------------------------------------------------------------------------
// _handle_lower_sync — Lower EL AArch64 synchronous exception
//
// Primary path for: HVC_AA64 (guest hypercalls), DATA_ABORT_LOWER (MMIO traps),
// INST_ABORT_LOWER.
// ---------------------------------------------------------------------------

_handle_lower_sync:
    TRAP_SAVE_CONTEXT               // x0 = TrapContext*
    bl el2_trap_lower_sync          // C++ handler (may advance ELR for HVC)
    b  _trap_restore_and_eret

// ---------------------------------------------------------------------------
// _handle_el2h_sync — EL2 self-trap (current-EL synchronous exception)
//
// Should not occur in normal operation. Dump and halt.
// ---------------------------------------------------------------------------

_handle_el2h_sync:
    TRAP_SAVE_CONTEXT               // x0 = TrapContext*
    bl el2_trap_current_sync        // C++ handler (dumps and halts)
    b  _trap_restore_and_eret       // unreachable if handler halts, but safe

// ---------------------------------------------------------------------------
// _handle_unhandled — catch-all for unexpected exceptions
// ---------------------------------------------------------------------------

_handle_unhandled:
    TRAP_SAVE_CONTEXT               // x0 = TrapContext*
    bl el2_trap_unhandled           // C++ handler (dumps and halts)
    b  _trap_restore_and_eret       // unreachable if handler halts, but safe

// ---------------------------------------------------------------------------
// _trap_restore_and_eret — restore all guest registers and return to guest
//
// On entry: sp points to the TrapContext frame.
// Restores x0–x30, SP_EL1, ELR_EL2, SPSR_EL2, then issues ERET.
// Note: ESR_EL2 and FAR_EL2 are read-only system registers; not restored.
// ---------------------------------------------------------------------------

_trap_restore_and_eret:
    // Restore ELR_EL2 and SPSR_EL2
    ldp x0, x1, [sp, #CTX_ELR]
    msr elr_el2,  x0
    msr spsr_el2, x1

    // Restore SP_EL1 and x30
    ldp x30, x0, [sp, #CTX_X30]
    msr sp_el1, x0

    // Restore x0–x29
    ldp x28, x29, [sp, #224]
    ldp x26, x27, [sp, #208]
    ldp x24, x25, [sp, #192]
    ldp x22, x23, [sp, #176]
    ldp x20, x21, [sp, #160]
    ldp x18, x19, [sp, #144]
    ldp x16, x17, [sp, #128]
    ldp x14, x15, [sp, #112]
    ldp x12, x13, [sp, #96]
    ldp x10, x11, [sp, #80]
    ldp x8,  x9,  [sp, #64]
    ldp x6,  x7,  [sp, #48]
    ldp x4,  x5,  [sp, #32]
    ldp x2,  x3,  [sp, #16]
    ldp x0,  x1,  [sp, #0]

    // Deallocate TrapContext frame
    add sp, sp, #TRAP_CTX_SIZE

    eret
