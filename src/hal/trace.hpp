#pragma once

// hal/trace.hpp
//
// The one spelling of a trace emit, low enough in the tree that a hal
// path — a panic — can record without depending on the trace component.
// The rings it writes are nova's inline storage, placed by whoever can.

#include "hal/cpu.hpp"
#include "hal/timer.hpp"
#include "nova/trace.hpp"

#include <cstddef>
#include <cstdint>

namespace nova {

// The hot-path entry point. Reads which core it is on rather than being
// told, stamps with the hyp timer, and drops when the ring is unplaced.
inline void trace_emit(std::uint16_t type, std::uint32_t a, std::uint64_t b = 0, std::uint64_t c = 0) noexcept {
  const std::size_t cpu = cpu::id();
  trace::g_ring[cpu].emit(hyp_timer::now_relaxed(), type, static_cast<std::uint8_t>(cpu), a, b, c);
}

} // namespace nova
