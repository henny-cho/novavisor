/* nova/abi/trace_ring.h
 *
 * The T layer's wire format: per-CPU overwriting event rings in a
 * reserved physical region, written by EL2 and read by the host.
 *
 * Why a ring at all. The S layer samples state, so an event whose
 * residency in state space is a few dozen cycles is not merely hard to
 * catch — it is absent. Measured: the interrupt bind was never once
 * seen by polling, at 10 Hz or at 500 Hz. A ring records the event
 * instead of the state it briefly left behind, which turns the drain
 * interval from a limit on *coverage* into a budget for *latency*.
 *
 * Why overwriting, and why the host owns no index. Observation must not
 * be able to stall what it observes. If the reader held a read cursor
 * here, a closed browser tab would fill the ring and EL2 would have to
 * block or drop; instead the writer always writes, laps when full, and
 * the reader keeps its cursor on its own side and computes what it
 * missed:
 *
 *   lost   = max(0, head - CAPACITY - cursor)
 *   window = [max(cursor, head - CAPACITY), head)
 *
 * A record body is written before `head` is published with a release
 * store, so a slot at or beyond `head` is never read. A reader re-reads
 * `head` after copying: anything the writer lapped during the copy has
 * fallen out of the window and is discarded rather than trusted.
 *
 * One ring per CPU, so the write path needs no cross-core atomic — a
 * core owns its ring outright. Timestamps come from CNTPCT_EL0, which
 * is common to all PEs, so merging the streams by `ts` recovers the
 * real global order.
 *
 * The region carries a header describing its own geometry. A reader
 * that finds the wrong magic or version fails loudly rather than
 * decoding a stale layout into plausible nonsense.
 *
 * Plain #defines only: this header is the single source for the C++
 * writer and the Python reader alike.
 */

#ifndef NOVA_TRACE_RING_H
#define NOVA_TRACE_RING_H

/* Macros are the point here (a non-C++ consumer parses this file) — the
 * usual constexpr guidance does not apply. */
// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

/* 'NVTRACE\0' little-endian, and the version of everything below it. */
#define NOVA_TRACE_MAGIC   0x004543415254564E
#define NOVA_TRACE_VERSION 1

/* Region header (64 B, one cache line). */
#define NOVA_TRACE_MAGIC_OFF   0x00
#define NOVA_TRACE_VERSION_OFF 0x08
#define NOVA_TRACE_RECSIZE_OFF 0x0C
#define NOVA_TRACE_STRIDE_OFF  0x10
#define NOVA_TRACE_RINGS_OFF   0x14
#define NOVA_TRACE_CAP_OFF     0x18
#define NOVA_TRACE_FREQ_OFF    0x1C /* CNTFRQ_EL0: ts -> seconds, one source */
#define NOVA_TRACE_HEADER_SIZE 0x40

/* Per-ring header, then the records. `head` sits alone on its line: it
 * is the only field both sides touch. */
#define NOVA_TRACE_HEAD_OFF    0x00
#define NOVA_TRACE_RECORDS_OFF 0x40

/* One record. A power of two, so indexing is a mask and no record
 * straddles a cache line.
 *
 * 32 bytes rather than 16: three argument words mean an event can carry
 * {vm, vintid, pintid, generation} as itself. Folding four values into
 * six spare bytes would be the kind of packing this layer exists to
 * avoid, and the region has room to spare. */
#define NOVA_TRACE_REC_SIZE 32
#define NOVA_TRACE_TS_OFF   0x00 /* u64 CNTPCT_EL0 */
#define NOVA_TRACE_TYPE_OFF 0x08 /* u16 NOVA_TRACE_EV_* */
#define NOVA_TRACE_CPU_OFF  0x0A /* u8  physical core */
#define NOVA_TRACE_FLAG_OFF 0x0B /* u8  reserved */
#define NOVA_TRACE_A_OFF    0x0C /* u32 */
#define NOVA_TRACE_B_OFF    0x10 /* u64 */
#define NOVA_TRACE_C_OFF    0x18 /* u64 */

/* Records per ring. 4096 * 32 B = 128 KiB each; four of those plus
 * headers fit the reserved region with room left. At a 20 Hz drain that
 * is ~82k events per second per core before anything is lost. */
#define NOVA_TRACE_CAPACITY 4096

/* Rings the region is sized for. The board decides how many it fills;
 * this is the ceiling every board's reservation must cover, so porting
 * to a wider machine does not silently overrun into the pristine
 * images. */
#define NOVA_TRACE_MAX_RINGS 4

/* The whole reserved region: header + MAX_RINGS * (header + records),
 * rounded up. Bounded by the 1020 KiB gap every board leaves between
 * the IVC page and the pristine images. */
#define NOVA_TRACE_SIZE 0x000A0000 /* 640 KiB */

/* Event kinds. The bridge's event catalogue names the same moments for
 * its breakpoints; a stop point and a trace hook are one fact about the
 * firmware, so the numbering here and the names there stay together. */
#define NOVA_TRACE_EV_TRAP         1
#define NOVA_TRACE_EV_VGIC_BIND    2
#define NOVA_TRACE_EV_VGIC_POST    3
#define NOVA_TRACE_EV_VGIC_PRIVATE 4
#define NOVA_TRACE_EV_VGIC_INJECT  5
#define NOVA_TRACE_EV_VGIC_EOI     6
#define NOVA_TRACE_EV_SCHED_SWITCH 7
#define NOVA_TRACE_EV_MMIO         8

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_TRACE_RING_H */
