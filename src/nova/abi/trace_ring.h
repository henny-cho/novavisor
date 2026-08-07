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
 *   window = [max(cursor, head - capacity + 1), head)
 *   lost   = (head - cursor) - |window|
 *
 * The `+ 1` is the whole depth story. Head at H means the writer has
 * published H records and is inside the slot for index H — the same
 * slot index H - capacity occupies — so that record is already being
 * destroyed and the recoverable depth is capacity - 1. Keeping the
 * `capacity`th would hand out one record assembled from two events.
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
 * It also carries the one loss the ring protocol above cannot express:
 * events emitted before the region was placed have no ring to land in,
 * and dropping them silently would make the header's own account of the
 * run incomplete at exactly the moment — early boot — a reader is most
 * likely to be looking for something. They are counted and published
 * beside the geometry.
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
#define NOVA_TRACE_VERSION 2

/* Region header (64 B, one cache line). */
#define NOVA_TRACE_MAGIC_OFF   0x00
#define NOVA_TRACE_VERSION_OFF 0x08
#define NOVA_TRACE_RECSIZE_OFF 0x0C
#define NOVA_TRACE_STRIDE_OFF  0x10
#define NOVA_TRACE_RINGS_OFF   0x14
#define NOVA_TRACE_CAP_OFF     0x18 /* records per ring, derived at placement */
#define NOVA_TRACE_FREQ_OFF    0x1C /* CNTFRQ_EL0: ts -> seconds, one source */
#define NOVA_TRACE_EARLY_OFF   0x20 /* u32 events emitted before placement */
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

/* The smallest ring worth placing, in records.
 *
 * Capacity is deliberately not a constant. It falls out of the region a
 * board reserves divided by the rings that board fills, and the writer
 * publishes the result in the header the reader already parses — so the
 * whole sizing decision is one number per board and there is no second
 * number to keep in agreement with it. A two-core board gets twice the
 * depth of a four-core one out of the same reservation, because the
 * divisor is the real ring count rather than the ceiling.
 *
 * What a floor buys is that a board reserving too little fails to build
 * rather than quietly handing the T layer a ring that laps inside one
 * drain interval. */
#define NOVA_TRACE_MIN_CAPACITY 4096

/* The ABI ceiling on the header's `rings` field, and the number of
 * rings the writer keeps inline storage for. A reader sizes nothing
 * from a header it has not yet vetted, so this is also what an
 * implausible `rings` is checked against. */
#define NOVA_TRACE_MAX_RINGS 4

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
#define NOVA_TRACE_EV_GIC_ACK      9
#define NOVA_TRACE_EV_CROSS_CALL   10
#define NOVA_TRACE_EV_IVC_DOORBELL 11
#define NOVA_TRACE_EV_PSCI         12
#define NOVA_TRACE_EV_UART_LINE    13
#define NOVA_TRACE_EV_SMMU_FAULT   14

/* Codes the host writes into the same stream, far above the firmware's
 * numbering and read as a separate family — so one can never arrive as
 * a hook nobody implemented.
 *
 * The ring protocol above can say how much a reader missed but not
 * where, and a count is the wrong shape for that knowledge: a drain
 * holds both ends of the hole and throws them away on the way out.
 * Written as a record instead, a hole sorts by timestamp, decodes,
 * lands in a lane and answers a window like anything else, so nothing
 * downstream needs a second path for the part of the run that is
 * missing. */
#define NOVA_TRACE_HOST_CODE_BASE 0x8000

/* A stretch nothing was watching: a ring lapped before a drain reached
 * it, or the events predate the region entirely. `ts` closes the hole
 * at the first record that survived it, `cpu` names the ring, `a`
 * counts what was lost, and `b` opens it at the last record handed out
 * before — zero when even that is unknown. */
#define NOVA_TRACE_HOST_EV_GAP 0x8000

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_TRACE_RING_H */
