#pragma once

// components/core_timer/include/core_timer/legacy_slot_model.hpp
//
// Pure claim policy for the legacy one-shot slot, host-testable. The
// slot is single per core, so who owns it — and when a second arm is a
// re-arm rather than a conflict — is the whole contract HVC_TIMER_SET
// exposes to guests.

#include <cstddef>

namespace nova::core_timer {

// One legacy one-shot per core (the slot itself lives in the per-core
// soft_timer queue): owner/armed pair guards double-arming. Arming and
// expiry both happen on the owner's core.
struct LegacySlot {
  std::size_t owner = 0;     // VCPU that armed the running one-shot
  bool        armed = false; // guards owner and rejects double-arming
};

// A free slot, or a re-arm by the VCPU already owning it, is accepted
// and stamped with the caller (re-arming moves the deadline, which is
// the owner's own business). An armed slot held by another VCPU is
// denied — dropping its pending expiry would lose that VCPU's timer.
[[nodiscard]] constexpr auto try_claim(LegacySlot& slot, std::size_t caller) noexcept -> bool {
  if (slot.armed && slot.owner != caller) {
    return false;
  }
  slot.owner = caller;
  slot.armed = true;
  return true;
}

// Expiry frees the slot; the stale owner stamp is unread while disarmed
// and gets overwritten by the next claim.
constexpr void release(LegacySlot& slot) noexcept {
  slot.armed = false;
}

// The claim policy as one run through the slot's life. What it pins is
// the losing case: a denied claim must leave the armed slot exactly as
// it was, or the owner's pending expiry disappears with no diagnostic.
static_assert(
    [] {
      LegacySlot slot{};
      const bool fresh   = !slot.armed; // nothing armed before a guest asks
      const bool claimed = try_claim(slot, 3) && slot.armed && slot.owner == 3U;
      const bool rearmed = try_claim(slot, 3) && slot.owner == 3U; // the owner moves its own deadline
      const bool denied  = !try_claim(slot, 5) && slot.armed && slot.owner == 3U;
      release(slot);
      const bool reopened = !slot.armed && try_claim(slot, 5) && slot.owner == 5U;
      release(slot);
      release(slot); // expiry may free a slot the guest never re-arms
      return fresh && claimed && rearmed && denied && reopened && !slot.armed && try_claim(slot, 0);
    }(),
    "the legacy slot takes any claim it is free for and refuses every other VCPU while armed");

} // namespace nova::core_timer
