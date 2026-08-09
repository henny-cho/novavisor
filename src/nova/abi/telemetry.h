/* nova/abi/telemetry.h
 *
 * The S layer's wire format: firmware state published on purpose,
 * rather than read behind the firmware's back.
 *
 * What this replaces, and what it does not. The host reads EL2 globals
 * today by resolving a symbol against the debug ELF and decoding the
 * bytes at that address out of QEMU's RAM file. Two different things
 * are bolted together there. Reading the *layout* from the compiler's
 * own DWARF is a derivation, and a good one — the same principle the
 * descriptor and register readers already use, and the alternative is
 * restating every field by hand in a header that then has to be kept
 * true. Reading the *bytes* at an arbitrary address, at an arbitrary
 * moment, through a file only QEMU provides, is the part that is a
 * workaround. So this ABI publishes bytes and says when they are
 * consistent; it says nothing about what the bytes mean. DWARF keeps
 * that job.
 *
 * A slot is one firmware global, copied. The copy is what makes a
 * snapshot possible at all: a reader cannot ask a running machine to
 * hold still, but the machine can take its own reading and say so.
 *
 * The sequence word answers two questions with one mechanism. Odd means
 * a write is in progress, so a reader that sees it retries — that is
 * the torn-read half. And the writer leaves it alone when the copy came
 * out identical, so "the sequence moved" means "the value moved" rather
 * than "a period elapsed" — that is the change-gate half. The second
 * one is why a reader can skip decoding what it already has, and why a
 * future transport narrower than a memory map could ship only what
 * changed.
 *
 * A slot is identified by the address of the global it copies. The
 * firmware therefore carries no table of names, no hashes and no topic
 * numbers: the host already resolves those symbols to addresses, so the
 * identity is a fact both sides derive rather than a third one to keep
 * in agreement. A symbol that moved or vanished is simply not found,
 * which is loud, where a name table would go on matching a slot that
 * now describes something else.
 *
 * Slots are self-contained. A descriptor names its own payload, its own
 * sequence and its own timestamp; nothing is shared across slots and
 * nothing is region-wide. One slot can be handed on alone.
 *
 * The timestamp is CNTPCT_EL0 — the clock the trace ring stamps its
 * records with. A reading and the events around it therefore land on
 * one axis, where a host arrival time would place the reading wherever
 * the poller happened to get to it.
 *
 * Plain #defines only: this header is the single source for the C++
 * publisher and the Python reader alike.
 */

#ifndef NOVA_TELEMETRY_H
#define NOVA_TELEMETRY_H

/* Macros are the point here (a non-C++ consumer parses this file) — the
 * usual constexpr guidance does not apply. */
// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

/* 'NVTLM\0\0\0' little-endian, and the version of everything below it. */
#define NOVA_TLM_MAGIC   0x0000004D4C54564E
#define NOVA_TLM_VERSION 1

/* Region header (one cache line).
 *
 * The period and the budget are here because EL2 decides them: it
 * publishes on a timer of its own and copies at most so much per turn,
 * so how stale a reading may be is a number the firmware declares. A
 * reader divides the total by the budget to learn how many turns a full
 * sweep takes, and multiplies by the period to get the bound. */
#define NOVA_TLM_MAGIC_OFF    0x00
#define NOVA_TLM_VERSION_OFF  0x08
#define NOVA_TLM_SLOTS_OFF    0x0C /* u32 slots declared at init */
#define NOVA_TLM_MAXSLOTS_OFF 0x10 /* u32 the ceiling this build offers */
#define NOVA_TLM_DESCSIZE_OFF 0x14 /* u32 descriptor stride */
#define NOVA_TLM_PERIOD_OFF   0x18 /* u32 microseconds between turns */
#define NOVA_TLM_BUDGET_OFF   0x1C /* u32 bytes one turn may copy */
#define NOVA_TLM_BYTES_OFF    0x20 /* u32 bytes all slots hold together */
#define NOVA_TLM_FREQ_OFF     0x24 /* u32 CNTFRQ_EL0: stamp -> seconds */
#define NOVA_TLM_HEADER_SIZE  0x40

/* One descriptor. Fixed stride, so the table is indexable and a reader
 * resolves a slot once at attach instead of walking on every poll —
 * payloads are packed densely behind it and vary by two orders of
 * magnitude, which a stride could only accommodate by wasting.
 *
 * `seq` sits beside the payload it guards rather than in a table of its
 * own: a reader polling for change touches one line per slot, and a
 * slot handed on alone carries its own guard. */
#define NOVA_TLM_DESC_SIZE       32
#define NOVA_TLM_DESC_SOURCE_OFF 0x00 /* u64 address of the global copied */
#define NOVA_TLM_DESC_SEQ_OFF    0x08 /* u64 odd while writing, else even */
#define NOVA_TLM_DESC_STAMP_OFF  0x10 /* u64 CNTPCT_EL0 when it last moved */
#define NOVA_TLM_DESC_AT_OFF     0x18 /* u32 payload offset from region base */
#define NOVA_TLM_DESC_BYTES_OFF  0x1C /* u32 payload bytes */

/* The descriptor table shares the first page with the header; payloads
 * start on the next one. A reader maps the region read-only and a
 * publisher writes nothing outside it, so the boundary is the size
 * rather than a promise. */
#define NOVA_TLM_MAX_SLOTS   64
#define NOVA_TLM_DESCS_OFF   NOVA_TLM_HEADER_SIZE
#define NOVA_TLM_PAYLOAD_OFF 0x1000

/* Room for what is declared today with the same margin the trace region
 * carries: the measured spans total a little over 20 KiB. Overrunning
 * it is a slot that never gets published and says so, not a slot that
 * lands on top of another. */
#define NOVA_TLM_PAYLOAD_BYTES 0x8000
#define NOVA_TLM_REGION_SIZE   (NOVA_TLM_PAYLOAD_OFF + NOVA_TLM_PAYLOAD_BYTES)

/* Payloads are placed at this granularity so the copy can move words
 * where the span allows. EL2 cannot call memcpy — the libc routines
 * reach for SIMD and EL2 must stay FP-free — so the copy is written
 * out, and alignment is the difference between a word loop and a byte
 * loop for the largest spans. */
#define NOVA_TLM_ALIGN 8

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_TELEMETRY_H */
