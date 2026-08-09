/* nova/abi/command_ring.h
 *
 * The host's way in: a ring the workbench writes and EL2 consumes, laid
 * over one page of hypervisor RAM.
 *
 * The trace ring's policy, reversed. Observation must not stall what it
 * observes, so that ring overwrites; control must not vanish, so this
 * one *refuses* when full and the producer learns immediately. A
 * command silently overwritten is a button that did nothing.
 *
 * The protocol is the IVC rings' SPSC shape: the producer owns `widx`,
 * the consumer owns `ridx`, neither side does a read-modify-write, and
 * a record body is published by the release store on `widx`. Both
 * indices live in the page, so a bridge that reconnects mid-run picks
 * the sequence up where the last one left it.
 *
 * The consumer trusts none of it: `ridx % NOVA_CMD_SLOTS` cannot leave
 * the page, every field is range-checked in EL2, and a drain stops at
 * the `widx` it read on entry.
 *
 * There is no acknowledgement channel. EL2 answers by emitting a trace
 * record, which puts a command and the effects it caused on one axis in
 * one clock and gives the answer the timeline, the recording and the
 * replay for free.
 *
 * Plain #defines only: this header is the single source for the C++
 * consumer and the Python producer alike.
 */

#ifndef NOVA_COMMAND_RING_H
#define NOVA_COMMAND_RING_H

/* Macros are the point here (a non-C++ consumer parses this file) — the
 * usual constexpr guidance does not apply. */
// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

/* 'NVCMD\0\0\0' little-endian, and the version of everything below it. */
#define NOVA_CMD_MAGIC   0x000000444D43564E
#define NOVA_CMD_VERSION 1

/* Header (one cache line), then the two indices on lines of their own:
 * they are the only fields both sides touch, and from opposite
 * directions.
 *
 * The period is here because EL2 decides it — it drains on a timer of
 * its own, so how long a command may wait is a number the firmware
 * declares rather than a property of how busy the machine is. Read from
 * anywhere else it would be a copy a changed period invalidates. */
#define NOVA_CMD_MAGIC_OFF   0x00
#define NOVA_CMD_VERSION_OFF 0x08
#define NOVA_CMD_RECSIZE_OFF 0x0C
#define NOVA_CMD_SLOTS_OFF   0x10
#define NOVA_CMD_PERIOD_OFF  0x14 /* u32 microseconds between drains */

/* What EL2 will accept, alongside how long it will make you wait. A
 * host that reads the bands here offers only values the machine takes;
 * anywhere else they would be a copy of whatever EL2 was built from,
 * true until the firmware changed without the host. */
#define NOVA_CMD_SLICE_MIN_OFF 0x18 /* u32 microseconds, narrowest quantum */
#define NOVA_CMD_SLICE_DEF_OFF 0x1C /* u32 microseconds, the boot value */
#define NOVA_CMD_SLICE_MAX_OFF 0x20 /* u32 microseconds, widest quantum */
#define NOVA_CMD_SPI_LO_OFF    0x24 /* u32, lowest virtual INTID accepted */
#define NOVA_CMD_SPI_HI_OFF    0x28 /* u32, highest, inclusive */

#define NOVA_CMD_WIDX_OFF    0x40 /* u64, producer-owned: commands written */
#define NOVA_CMD_RIDX_OFF    0x80 /* u64, consumer-owned: commands taken */
#define NOVA_CMD_RECORDS_OFF 0xC0

/* One command: an opcode and two argument words, all at one width.
 *
 * The two words are the pair a trace record carries in `b` and `c`, so
 * a command and the record answering it hold their arguments in the
 * same order at the same width and neither side repacks.
 *
 * The offsets carry REC_ rather than sitting directly under NOVA_CMD_:
 * the opcodes below are read as a name family by prefix, and an offset
 * called NOVA_CMD_OP_OFF would join them as an opcode "off" worth 0. */
#define NOVA_CMD_REC_SIZE   24
#define NOVA_CMD_REC_OP_OFF 0x00 /* u64 NOVA_CMD_OP_* */
#define NOVA_CMD_REC_A_OFF  0x08 /* u64 */
#define NOVA_CMD_REC_B_OFF  0x10 /* u64 */

/* The page this lives in, and the depth that fills it: a power of two
 * so the slot index is a mask, and the largest one the page holds.
 *
 * A constant rather than the trace ring's division, because the region
 * is a page by choice rather than a board's reservation — the host maps
 * exactly this much read-write and nothing else, so the size *is* the
 * boundary and no board has a say in it. */
#define NOVA_CMD_PAGE  4096
#define NOVA_CMD_SLOTS 128

/* What the host may ask for. Every opcode runs code in EL2 even when
 * the effect is a single store: one entry point is one place that
 * validates, and the write window stays the only way in. */
#define NOVA_CMD_OP_MARK  1 /* a, b: free tags — records the moment and nothing else */
#define NOVA_CMD_OP_SPI   2 /* a: VM index, b: virtual INTID */
#define NOVA_CMD_OP_SLICE 3 /* a: scheduler slice, in microseconds */

/* How the answering trace record carries both: the opcode in the low
 * half of its first word, the result in the high half, both being
 * small. Declared here so the writer and the reader cannot invent
 * different halves — and named ANSWER rather than OP or RESULT, whose
 * prefixes are read as name families.
 *
 * No opcode is zero, so zero is what an opcode too wide for the field
 * is reported as: unnameable rather than reported as some other op. */
#define NOVA_CMD_ANSWER_SHIFT 16
#define NOVA_CMD_ANSWER_MASK  0xFFFF

/* What became of it. Carried in the answering trace record beside the
 * opcode, so a refusal is as visible as an acceptance.
 *
 * FULL is the exception: the ring had no slot, so EL2 never saw the
 * command and writes no record for it — the producer knows at once and
 * says so itself. Named here anyway, because why a command did not
 * happen is one vocabulary and a reader decoding refusals should not
 * need a second table for the one EL2 cannot express. */
#define NOVA_CMD_RESULT_OK      0
#define NOVA_CMD_RESULT_UNKNOWN 1 /* an opcode this build does not implement */
#define NOVA_CMD_RESULT_RANGE   2 /* an argument outside what EL2 accepts */
#define NOVA_CMD_RESULT_STATE   3 /* well formed, but not something to do now */
#define NOVA_CMD_RESULT_FULL    4 /* producer-side: no slot, never delivered */

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_COMMAND_RING_H */
