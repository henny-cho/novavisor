/* nova/abi/ivc_ring.h
 *
 * Lock-free SPSC ring layout inside the IVC shared page — the single
 * source consumed by the hypervisor-side C++ model
 * (components/ivc/include/ivc/ring.hpp, host-tested) and the guest C
 * helper (demo/common/include/guest_ring.h).
 *
 * Two directional rings (one producer, one consumer each — no RMW
 * anywhere, so the protocol needs only load-acquire/store-release and
 * works on memory the guests see as Device):
 *
 *   ring 0 at +NOVA_IVC_RING0_OFF   (convention: lower VM index -> higher)
 *   ring 1 at +NOVA_IVC_RING1_OFF   (the reverse direction)
 *
 * Ring layout (per ring, offsets from the ring base):
 *   +0x00  widx (u32) — slots produced, mod-free monotonic; producer-owned
 *   +0x40  ridx (u32) — slots consumed;                     consumer-owned
 *   +0x80  slots[NOVA_IVC_RING_SLOTS] of u64 payloads
 *
 * widx/ridx sit on separate 64-byte lines so the two sides never write
 * the same line. Indices wrap naturally (power-of-two slot count);
 * empty: widx == ridx, full: widx - ridx == NOVA_IVC_RING_SLOTS.
 *
 * The tail of the page (0xF80..) stays free for demo scratch state
 * (e.g. the lifecycle demo's boot counters).
 *
 * Plain #defines only: this header must survive C and C++ alike.
 */

#ifndef NOVA_IVC_RING_H
#define NOVA_IVC_RING_H

/* Macros are the point here (C consumers) — the usual constexpr
 * guidance does not apply. */
// NOLINTBEGIN(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#define NOVA_IVC_RING0_OFF 0x000
#define NOVA_IVC_RING1_OFF 0x800

#define NOVA_IVC_RING_WIDX_OFF  0x00
#define NOVA_IVC_RING_RIDX_OFF  0x40
#define NOVA_IVC_RING_SLOTS_OFF 0x80

#define NOVA_IVC_RING_SLOTS 16 /* power of two; capacity == SLOTS */

// NOLINTEND(cppcoreguidelines-macro-usage, cppcoreguidelines-macro-to-enum, modernize-macro-to-enum)

#endif /* NOVA_IVC_RING_H */
