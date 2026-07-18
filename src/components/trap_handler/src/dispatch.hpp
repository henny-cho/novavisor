#pragma once

// Internal entry points shared between the router TU and the per-class
// dispatch TUs. Not part of the component's public headers.

#include "nova/arch/trap_context.hpp"

namespace nova::trap {

// Stage 2 Data Abort from the guest (EC 0x24): decode, emulate through
// MmioService, escalate to GuestFaultService when not emulatable.
void dispatch_data_abort(TrapContext* ctx) noexcept;

// Escalate an unrecoverable guest fault. When a subscriber claims it,
// it has retired the faulting VCPU and swapped the live frame to the
// next runnable one — returning resumes that guest. Unclaimed means
// nobody owns VM lifecycles: stop the machine.
void dispatch_guest_fault(TrapContext* ctx) noexcept;

} // namespace nova::trap
