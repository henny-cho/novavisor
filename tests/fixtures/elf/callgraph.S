// callgraph.S — oracle fixture for the ELF structure analyser.
//
// A tiny AArch64 image whose call graph is known by construction, so
// `elfstruct.analyse()` can be checked against a hand-written expectation
// (callgraph.expect.json) rather than against itself. Every shape the
// contract names appears exactly once:
//
//   direct call     fx_entry --bl--> fx_direct
//   tail branch     fx_tail_from_entry --b--> fx_tail_target
//   indirect site   fx_entry: blr through an address taken with adrp/add
//                   (an address load is not an edge, so fx_indirect_target
//                    must come back unproven — the same shape a cib service
//                    slot has in the real image)
//   vector entry    _vector_table's slots branch to fx_slot_*
//   unreferenced    fx_orphan and its callee: reachable from nothing
//   outside the set fx_data (OBJECT) and fx_marker (NOTYPE, size 0) must be
//                   ignored, not rejected — only FUNC symbols are analysed
//
// Every routine carries .type/.size: the analyser rejects a zero-size FUNC,
// and this fixture is also what proves that rejection is not reached here.
// Instruction counts are noted so the expected sizes in the oracle can be
// read off the source instead of trusted from a tool.

.section .text.fx, "ax", @progbits

// --- entry point (root: ELF e_entry) -------------------------------------
.global fx_entry
.type   fx_entry, @function
fx_entry:
    bl      fx_direct                       // direct edge
    adrp    x0, fx_indirect_target          // address load — NOT an edge
    add     x0, x0, :lo12:fx_indirect_target
    blr     x0                              // indirect site
    b       fx_tail_from_entry              // tail edge
.size fx_entry, . - fx_entry                // 5 instructions = 20 B

.type   fx_direct, @function
fx_direct:
    mov     x1, #1
    ret
.size fx_direct, . - fx_direct              // 2 = 8 B

.type   fx_tail_from_entry, @function
fx_tail_from_entry:
    mov     x2, #2
    b       fx_tail_target                  // tail edge
.size fx_tail_from_entry, . - fx_tail_from_entry  // 2 = 8 B

.type   fx_tail_target, @function
fx_tail_target:
    ret
.size fx_tail_target, . - fx_tail_target    // 1 = 4 B

// --- vector stubs (roots: branch targets inside _vector_table) -----------
.type   fx_slot_sync, @function
fx_slot_sync:
    bl      fx_from_sync
    ret
.size fx_slot_sync, . - fx_slot_sync        // 2 = 8 B

.type   fx_slot_irq, @function
fx_slot_irq:
    ret
.size fx_slot_irq, . - fx_slot_irq          // 1 = 4 B

.type   fx_from_sync, @function
fx_from_sync:
    ret
.size fx_from_sync, . - fx_from_sync        // 1 = 4 B

// --- reachable only through a function pointer --------------------------
.type   fx_indirect_target, @function
fx_indirect_target:
    ret
.size fx_indirect_target, . - fx_indirect_target  // 1 = 4 B

// --- referenced by nothing ----------------------------------------------
.type   fx_orphan, @function
fx_orphan:
    bl      fx_orphan_callee
    ret
.size fx_orphan, . - fx_orphan              // 2 = 8 B

.type   fx_orphan_callee, @function
fx_orphan_callee:
    ret
.size fx_orphan_callee, . - fx_orphan_callee  // 1 = 4 B

// A code-section label with no type and no size. Outside the analysed set
// (FUNC only), so it must be ignored rather than rejected.
.global fx_marker
fx_marker:

// --- vector table: two 128-byte slots, entered by hardware --------------
// Same shape as the real table (a slot is `.align 7` + branch), so the slot
// decoder is exercised by size rather than by a hardcoded count of 16.
    .balign 2048
.global _vector_table
.type   _vector_table, @function
_vector_table:
    .align 7
    b       fx_slot_sync                    // slot 0
    .align 7
    b       fx_slot_irq                     // slot 1
    .align 7
.size _vector_table, . - _vector_table      // 2 slots x 128 = 256 B

// --- outside the analysed set: data -------------------------------------
.section .rodata.fx, "a", @progbits
.global fx_data
.type   fx_data, @object
fx_data:
    .word   0
.size fx_data, . - fx_data
