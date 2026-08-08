/* nova/abi/command_ring.h
 *
 * The host's way in: a single-slot-per-command ring the workbench
 * writes and EL2 consumes, laid over one page of hypervisor RAM.
 *
 * The mirror image of the trace ring, and deliberately the opposite
 * policy. Observation must not be able to stall what it observes, so
 * that ring overwrites and the reader computes what it missed. Control
 * must not be able to vanish, so this one *refuses* when full and the
 * producer learns immediately. A command silently overwritten is a
 * button that did nothing, which is worse than a button that said no.
 *
 * Direction is the only thing reversed; the protocol is the same SPSC
 * shape as the IVC rings. The producer owns `widx`, the consumer owns
 * `ridx`, neither side does a read-modify-write, and a record body is
 * published by the release store on `widx`.
 *
 * Both indices live in the shared page rather than in either side's
 * private memory. A bridge that reconnects mid-run picks the sequence
 * up where the last one left it, so a command's position in the stream
 * is a property of the ring and not of whoever is currently attached.
 *
 * The consumer trusts none of it. `ridx % NOVA_CMD_SLOTS` cannot leave
 * the page whatever the producer writes, every field is range-checked
 * on the EL2 side, and a drain stops at the `widx` it read on entry —
 * so a hostile producer can lengthen no callback and reach nothing but
 * its own refusals.
 *
 * There is no acknowledgement channel. EL2 answers by emitting a trace
 * record, which puts a command and the effects it caused on one axis in
 * one clock, and gives the answer the timeline, the recording and the
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
 * they are the only fields both sides touch, and they are touched from
 * opposite directions. */
#define NOVA_CMD_MAGIC_OFF   0x00
#define NOVA_CMD_VERSION_OFF 0x08
#define NOVA_CMD_RECSIZE_OFF 0x0C
#define NOVA_CMD_SLOTS_OFF   0x10
#define NOVA_CMD_WIDX_OFF    0x40 /* u64, producer-owned: commands written */
#define NOVA_CMD_RIDX_OFF    0x80 /* u64, consumer-owned: commands taken */
#define NOVA_CMD_RECORDS_OFF 0xC0

/* One command: an opcode and two argument words, all at one width.
 *
 * The two words are the same pair a trace record carries in `b` and
 * `c`, so a command and the record that answers it hold their arguments
 * in the same order at the same width and neither side repacks. A
 * uniform width rather than a tighter mix because the whole point of
 * this page is that one description of it is enough. */
#define NOVA_CMD_REC_SIZE 24
#define NOVA_CMD_OP_OFF   0x00 /* u64 NOVA_CMD_OP_* */
#define NOVA_CMD_A_OFF    0x08 /* u64 */
#define NOVA_CMD_B_OFF    0x10 /* u64 */

/* The page this all lives in, and the depth that fills it.
 *
 * A power of two so the slot index is a mask, and the largest one the
 * page holds — the next size up needs 6 KiB. Unlike the trace ring's
 * depth this is a constant rather than a division, because the region
 * is a page by choice rather than a board's reservation: the host maps
 * exactly this much read-write and nothing else, so the size *is* the
 * boundary and there is nothing left for a board to decide. */
#define NOVA_CMD_PAGE  4096
#define NOVA_CMD_SLOTS 128

/* What the host may ask for. Every opcode runs code in EL2 even when
 * the effect is a single store: one entry point means one place that
 * validates, and the write window stays the only way in. */
#define NOVA_CMD_OP_MARK  1 /* a, b: free tags — records the moment and nothing else */
#define NOVA_CMD_OP_SPI   2 /* a: VM index, b: virtual INTID */
#define NOVA_CMD_OP_SLICE 3 /* a: scheduler slice, in microseconds */

/* What became of it. Carried in the answering trace record beside the
 * opcode, so a refusal is as visible as an acceptance and says which
 * kind it was.
 *
 * FULL is the exception: the ring had no slot, so EL2 never saw the
 * command and writes no record for it. The producer knows at once and
 * says so itself. It is named here anyway because the reason a command
 * did not happen is one vocabulary — a reader decoding refusals should
 * not need a second table for the one nobody in EL2 can express. */
#define NOVA_CMD_RESULT_OK      0
#define NOVA_CMD_RESULT_UNKNOWN 1 /* an opcode this build does not implement */
#define NOVA_CMD_RESULT_RANGE   2 /* an argument outside what EL2 accepts */
#define NOVA_CMD_RESULT_STATE   3 /* well formed, but not something to do now */
#define NOVA_CMD_RESULT_FULL    4 /* producer-side: no slot, never delivered */

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_COMMAND_RING_H */
