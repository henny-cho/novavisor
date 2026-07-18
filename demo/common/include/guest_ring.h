/* demo/common/include/guest_ring.h
 *
 * Guest-side SPSC ring over the IVC shared page — the C twin of the
 * hypervisor's host-tested model (components/ivc/include/ivc/ring.hpp),
 * both speaking the nova/abi/ivc_ring.h layout.
 *
 * One VM produces into a ring, exactly one other consumes it — no
 * read-modify-write, only load-acquire/store-release, so the protocol
 * is sound even on memory the guest maps as Device (EL1 MMU off).
 *
 * Convention: ring 0 carries lower VM index -> higher, ring 1 the
 * reverse. A pingpong pair therefore agrees on direction by role.
 */

#ifndef GUEST_RING_H
#define GUEST_RING_H

#include "nova/abi/guest_layout.h"
#include "nova/abi/ivc_ring.h"

#include <stdint.h>

static inline volatile uint32_t* ring_widx(uintptr_t base) {
  return (volatile uint32_t*)(base + NOVA_IVC_RING_WIDX_OFF);
}

static inline volatile uint32_t* ring_ridx(uintptr_t base) {
  return (volatile uint32_t*)(base + NOVA_IVC_RING_RIDX_OFF);
}

static inline volatile uint64_t* ring_slots(uintptr_t base) {
  return (volatile uint64_t*)(base + NOVA_IVC_RING_SLOTS_OFF);
}

/* Ring bases inside the shared page as the guest sees it. */
static inline uintptr_t ring0_base(void) {
  return NOVA_IVC_SHM_IPA + NOVA_IVC_RING0_OFF;
}
static inline uintptr_t ring1_base(void) {
  return NOVA_IVC_SHM_IPA + NOVA_IVC_RING1_OFF;
}

/* Producer side. 0 when full. */
static inline int ring_push(uintptr_t base, uint64_t value) {
  const uint32_t w = __atomic_load_n(ring_widx(base), __ATOMIC_RELAXED); /* producer-owned */
  const uint32_t r = __atomic_load_n(ring_ridx(base), __ATOMIC_ACQUIRE);
  if (w - r == NOVA_IVC_RING_SLOTS) {
    return 0;
  }
  ring_slots(base)[w & (NOVA_IVC_RING_SLOTS - 1)] = value;
  __atomic_store_n(ring_widx(base), w + 1, __ATOMIC_RELEASE); /* publishes the payload */
  return 1;
}

/* Consumer side. 0 when empty. */
static inline int ring_pop(uintptr_t base, uint64_t* value) {
  const uint32_t r = __atomic_load_n(ring_ridx(base), __ATOMIC_RELAXED); /* consumer-owned */
  const uint32_t w = __atomic_load_n(ring_widx(base), __ATOMIC_ACQUIRE);
  if (w == r) {
    return 0;
  }
  *value = ring_slots(base)[r & (NOVA_IVC_RING_SLOTS - 1)];
  __atomic_store_n(ring_ridx(base), r + 1, __ATOMIC_RELEASE); /* frees the slot */
  return 1;
}

#endif /* GUEST_RING_H */
