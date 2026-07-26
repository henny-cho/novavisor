#pragma once

// components/trap_handler/include/trap_handler/mmio.hpp
//
// MmioService — one emulatable guest MMIO access (Stage 2 translation
// fault with a valid syndrome), dispatched to every subscriber.
// Subscribers claim the IPA ranges they emulate: act, set
// `handled = true`, and for reads leave the raw device value in
// `value` — the dispatcher performs the architectural widening into the
// guest register and advances ELR past the faulting instruction.
// Unclaimed accesses are dumped and escalate to GuestFaultService.

#include "nova/arch/trap_context.hpp"

#include <cstdint>
#include <nexus/callback.hpp>

namespace nova {

struct MmioCall {
  TrapContext*  ctx     = nullptr;
  std::uint64_t ipa     = 0;
  std::uint32_t size    = 0; // access size in bytes (1/2/4/8)
  bool          write   = false;
  std::uint64_t value   = 0; // write: guest data (truncated to size); read: result
  bool          handled = false;
};

struct MmioService : public callback::service<MmioCall*> {};

} // namespace nova
