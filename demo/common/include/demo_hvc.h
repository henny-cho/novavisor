// NovaVisor demo HVC helpers.
//
// Inline stubs for the NovaVisor guest hypercall ABI. Shared by every
// demo guest. Function IDs come from nova/abi/hvc_abi.h — the single source
// shared with the hypervisor's dispatcher (components/demo_hvc).
//
// Function ID lives in x0. Arguments in x1..x6. Return (if any) in x0.

#ifndef NOVAVISOR_DEMO_HVC_H
#define NOVAVISOR_DEMO_HVC_H

#include "nova/abi/hvc_abi.h"

#include <stddef.h>
#include <stdint.h>

enum {
  HVC_PUTS      = NOVA_HVC_FN_PUTS,
  HVC_PUTC      = NOVA_HVC_FN_PUTC,
  HVC_EXIT      = NOVA_HVC_FN_EXIT,
  HVC_YIELD     = NOVA_HVC_FN_YIELD,
  HVC_HEARTBEAT = NOVA_HVC_FN_HEARTBEAT,
  HVC_VM_START  = NOVA_HVC_FN_VM_START,
  // IVC range (Phase 7)
  HVC_IVC_DOORBELL = NOVA_HVC_FN_IVC_DOORBELL,
  // Timer range (Phase 6)
  HVC_TIMER_SET = NOVA_HVC_FN_TIMER_SET,
};

static inline void hvc_putc(char c) {
  register uint64_t x0 __asm__("x0") = HVC_PUTC;
  register uint64_t x1 __asm__("x1") = (uint64_t)(unsigned char)c;
  __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1) : "memory");
}

static inline void hvc_puts(const char* s, size_t n) {
  register uint64_t x0 __asm__("x0") = HVC_PUTS;
  register uint64_t x1 __asm__("x1") = (uint64_t)(uintptr_t)s;
  register uint64_t x2 __asm__("x2") = (uint64_t)n;
  __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1), "r"(x2) : "memory");
}

// Convenience: print a C string literal whose length the compiler knows.
#define hvc_puts_lit(s) hvc_puts((s), sizeof(s) - 1)

static inline void hvc_exit(int code) {
  register uint64_t x0 __asm__("x0") = HVC_EXIT;
  register uint64_t x1 __asm__("x1") = (uint64_t)code;
  __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1) : "memory");
  __builtin_unreachable();
}

static inline void hvc_yield(void) {
  register uint64_t x0 __asm__("x0") = HVC_YIELD;
  __asm__ volatile("hvc #0" : "+r"(x0)::"memory");
}

static inline void hvc_heartbeat(uint64_t vm_id) {
  register uint64_t x0 __asm__("x0") = HVC_HEARTBEAT;
  register uint64_t x1 __asm__("x1") = vm_id;
  __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1) : "memory");
}

// One-shot hypervisor timer: injects vINTID 27 (virtual timer PPI)
// after `ticks` counter cycles (CNTFRQ rate). Returns 0 on success.
static inline uint64_t hvc_timer_set(uint64_t ticks) {
  register uint64_t x0 __asm__("x0") = HVC_TIMER_SET;
  register uint64_t x1 __asm__("x1") = ticks;
  __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1) : "memory");
  return x0;
}

// Start a not-yet-running VM (guest_table index). The new VM runs when
// someone yields. Returns 0 on success.
static inline uint64_t hvc_vm_start(uint64_t vm_index) {
  register uint64_t x0 __asm__("x0") = HVC_VM_START;
  register uint64_t x1 __asm__("x1") = vm_index;
  __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1) : "memory");
  return x0;
}

// Ring the doorbell of another VM: injects the doorbell vIRQ
// (vINTID NOVA_IVC_DOORBELL_VINTID) into it. Returns 0 on success.
static inline uint64_t hvc_ivc_doorbell(uint64_t vm_index) {
  register uint64_t x0 __asm__("x0") = HVC_IVC_DOORBELL;
  register uint64_t x1 __asm__("x1") = vm_index;
  __asm__ volatile("hvc #0" : "+r"(x0) : "r"(x1) : "memory");
  return x0;
}

#endif // NOVAVISOR_DEMO_HVC_H
