#pragma once

// components/trap_handler/include/trap_handler/hvc.hpp
//
// HvcService — one guest HVC, dispatched to every subscriber. The
// function ID is read from ctx->x[0] (SMCCC convention — the
// `hvc #imm16` instruction's own immediate is always 0); the allocation
// table lives in nova/abi/hvc_abi.h (shared with the guests).
//
// Each subscriber inspects `func_id`, acts only on IDs it implements,
// and sets `handled = true` for those — silently returning otherwise.
// If no subscriber claims the call, trap_handler warns and returns
// SMCCC NOT_SUPPORTED (-1) in the guest's x0.

#include "nova/arch/trap_context.hpp"

#include <cstdint>
#include <nexus/callback.hpp>

namespace nova {

// SMCCC v1.x: unimplemented functions and invalid arguments return -1
// in x0. Shared by the dispatcher and every HvcService subscriber.
inline constexpr std::uint64_t kSmcccNotSupported = ~0ULL;

struct HvcCall {
  TrapContext* ctx = nullptr;
  // Full SMCCC function ID (x0 bits 31:0) — standard ranges like PSCI
  // (0x8400_xxxx) need all 32 bits; NOVA's own IDs stay below 0x10000.
  std::uint32_t func_id = 0;
  bool          handled = false;
};

struct HvcService : public callback::service<HvcCall*> {};

} // namespace nova
