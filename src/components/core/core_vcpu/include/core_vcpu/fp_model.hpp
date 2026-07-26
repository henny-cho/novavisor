#pragma once

// components/core_vcpu/include/core_vcpu/fp_model.hpp
//
// Lazy FP/SIMD ownership — pure decision model, host-testable.
//
// The hardware FP register file belongs to at most one VCPU (the
// owner); its bank copy is stale while it owns. Every other VCPU runs
// with the FP trap set and claims the file on first use. The model
// decides WHO saves/restores/traps; the mechanism (hal fp.hpp) moves
// the registers.

#include <cstddef>

namespace nova::fp {

inline constexpr std::size_t kNoOwner = static_cast<std::size_t>(-1);

class Ownership {
public:
  // First FP use by `current` (trap): `current` becomes owner. Returns
  // the previous owner whose live hardware state must be saved first —
  // kNoOwner when the register file carries no live guest state, or
  // `current` itself when the trap was spurious (already owner: nothing
  // to move).
  constexpr auto claim(std::size_t current) noexcept -> std::size_t {
    const std::size_t prev = owner_;
    owner_                 = current;
    return prev;
  }

  // Should FP accesses trap while `resident` runs? Only the owner may
  // touch the file untrapped.
  [[nodiscard]] constexpr auto trap_needed(std::size_t resident) const noexcept -> bool { return owner_ != resident; }

  // VCPU reseeded: any live hardware state it owns is garbage — drop
  // ownership so no one ever saves it. No-op for non-owners.
  constexpr void invalidate(std::size_t index) noexcept {
    if (owner_ == index) {
      owner_ = kNoOwner;
    }
  }

  [[nodiscard]] constexpr auto owner() const noexcept -> std::size_t { return owner_; }

private:
  std::size_t owner_ = kNoOwner;
};

} // namespace nova::fp
