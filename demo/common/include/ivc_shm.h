// NovaVisor demo IVC shared-memory mailbox.
//
// Guest-owned protocol on the shared page the hypervisor maps into
// every VM at NOVA_IVC_SHM_IPA (nova/abi/guest_layout.h). One mailbox per
// direction: the sender writes the payload, publishes it by bumping
// `seq`, then rings the peer's doorbell (hvc_ivc_doorbell).
//
// Concurrency note: VCPUs are time-shared cooperatively on one core —
// the peers never run concurrently — so a compiler barrier before the
// seq publish is all the ordering this protocol needs. A real SPSC
// ring with acquire/release atomics arrives with SMP.

#ifndef NOVAVISOR_DEMO_IVC_SHM_H
#define NOVAVISOR_DEMO_IVC_SHM_H

#include "nova/abi/guest_layout.h"

#include <stdint.h>

struct ivc_mailbox {
  volatile uint64_t seq;        // sender increments after writing payload
  volatile uint64_t payload[2]; // 16-byte message
};

// Mailbox pair at the head of the shared page, indexed by the RECEIVING
// VM's guest_table index (box[1] carries VM0→VM1 traffic and so on).
// Sized well under NOVA_IVC_SHM_SIZE.
struct ivc_shm {
  struct ivc_mailbox box[2];
};

#define IVC_SHM ((struct ivc_shm*)NOVA_IVC_SHM_IPA)

// Publish a 16-byte message. Follow with hvc_ivc_doorbell(receiver) to
// wake the peer.
static inline void ivc_send(struct ivc_mailbox* mb, uint64_t a, uint64_t b) {
  mb->payload[0] = a;
  mb->payload[1] = b;
  __asm__ volatile("" ::: "memory"); // payload visible before seq bump
  mb->seq = mb->seq + 1;
}

#endif // NOVAVISOR_DEMO_IVC_SHM_H
