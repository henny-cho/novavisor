#pragma once

// hal/console.hpp
//
// Board-agnostic console facade. This is the ONE place where generic code
// binds to the active board's UART.

#include "hal/board/active/uart.hpp"
#include "hal/cpu.hpp"
#include "nova/fmt.hpp"
#include "nova/sync.hpp"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>

namespace nova::console {

// The UART is one shared FIFO. Use write_parts for atomic multi-fragment lines.
inline sync::SpinLock g_lock;

// First-failure ownership (hal/panic.hpp claims it): while set, the
// owning core writes raw — the normal lock may be held by a core that
// is now parked — and every other core's output is dropped so nothing
// splices into the failure report.
inline constexpr std::size_t    kNoPanicOwner = ~std::size_t{0};
inline std::atomic<std::size_t> g_panic_owner{kNoPanicOwner};

namespace detail {
enum class Route : std::uint8_t { kLocked, kRaw, kDrop };

[[nodiscard]] inline auto route() noexcept -> Route {
  const std::size_t owner = g_panic_owner.load(std::memory_order_relaxed);
  if (owner == kNoPanicOwner) {
    return Route::kLocked;
  }
  return owner == cpu::id() ? Route::kRaw : Route::kDrop;
}
} // namespace detail

inline void write(std::string_view sv) noexcept {
  const auto route = detail::route();
  if (route == detail::Route::kDrop) {
    return;
  }
  if (route == detail::Route::kRaw) {
    board::active::Uart::write(sv);
    return;
  }
  sync::Guard guard{g_lock};
  board::active::Uart::write(sv);
}

// Null-terminated C strings (extern "C" boundaries, __func__-style values).
inline void write(const char* str) noexcept {
  write(std::string_view{str});
}

// Emit one logical line from preformatted fragments under one lock.
inline void write_parts(std::span<const std::string_view> parts) noexcept {
  const auto route = detail::route();
  if (route == detail::Route::kDrop) {
    return;
  }
  if (route == detail::Route::kRaw) {
    for (const std::string_view part : parts) {
      board::active::Uart::write(part);
    }
    return;
  }
  sync::Guard guard{g_lock};
  for (const std::string_view part : parts) {
    board::active::Uart::write(part);
  }
}

// Typed integer fragments for line(): formatted while the lock is held,
// so a line mixing text and numbers still leaves the UART atomically.
struct Dec {
  std::uint64_t v;
};
struct Hex {
  std::uint64_t v;
};

namespace detail {
inline void put(std::string_view sv) noexcept {
  board::active::Uart::write(sv);
}
inline void put(const char* str) noexcept {
  board::active::Uart::write(str);
}
inline void put(Dec d) noexcept {
  fmt::DecBuf buf{};
  board::active::Uart::write(fmt::to_dec64(d.v, buf));
}
inline void put(Hex h) noexcept {
  fmt::HexBuf buf{};
  board::active::Uart::write(fmt::to_hex64(h.v, buf));
}
} // namespace detail

// One logical line from mixed fragments (string_view/const char*/Dec/
// Hex) under one lock — multi-call sequences splice under SMP; this
// cannot.
template <typename... Parts>
inline void line(Parts... parts) noexcept {
  const auto route = detail::route();
  if (route == detail::Route::kDrop) {
    return;
  }
  if (route == detail::Route::kRaw) {
    (detail::put(parts), ...);
    return;
  }
  sync::Guard guard{g_lock};
  (detail::put(parts), ...);
}

// 16 zero-padded lowercase hex digits, no "0x" prefix.
inline void write_hex64(std::uint64_t v) noexcept {
  fmt::HexBuf buf{};
  write(fmt::to_hex64(v, buf));
}

// Base 10, no leading zeros.
inline void write_dec64(std::uint64_t v) noexcept {
  fmt::DecBuf buf{};
  write(fmt::to_dec64(v, buf));
}

// Host input: one RX byte, or -1 when none is waiting. Single consumer
// by construction (the UART interrupt is routed to one core), so the
// write lock is not involved — RX and TX are separate FIFOs.
[[nodiscard]] inline auto try_read() noexcept -> int {
  return board::active::Uart::try_read();
}

// The active board's console SPI — the INTID rx_irq_enable() arms.
inline constexpr std::uint32_t kRxIntid = board::active::kUartIntid;

// Unmask the console's RX interrupt at the device. GIC routing of the
// UART SPI stays with the caller (hal/gic.hpp enable_spi).
inline void rx_irq_enable() noexcept {
  board::active::Uart::rx_irq_enable();
}

} // namespace nova::console
