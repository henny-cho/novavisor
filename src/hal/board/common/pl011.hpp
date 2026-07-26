#pragma once

#include <cstdint>
#include <span>
#include <string_view>

namespace nova::board::common {

template <std::uintptr_t Base>
struct Pl011 {
  inline static constexpr std::uintptr_t kFlagOffset = 0x18;
  inline static constexpr std::uintptr_t kMaskOffset = 0x38;
  inline static constexpr std::uint32_t  kTxFull     = 1U << 5U;
  inline static constexpr std::uint32_t  kRxEmpty    = 1U << 4U;
  inline static constexpr std::uint32_t  kRxIrq      = 1U << 4U;

  // TX-full wait budget. At any real baud rate the FIFO drains orders
  // of magnitude faster than this spin; the bound only breaks a dead
  // or unclocked UART, where the alternative is a panic path that
  // hangs on its first character instead of reaching the park.
  inline static constexpr std::uint32_t kTxBudget = 1'000'000;

  static void write(std::span<const std::uint8_t> data) noexcept {
    auto* const       data_register = reinterpret_cast<volatile std::uint32_t*>(Base);
    const auto* const flags         = reinterpret_cast<const volatile std::uint32_t*>(Base + kFlagOffset);
    for (const std::uint8_t byte : data) {
      for (std::uint32_t budget = kTxBudget; (*flags & kTxFull) != 0U && budget != 0U; --budget) {
      }
      *data_register = byte;
    }
  }

  static void write(std::string_view text) noexcept {
    write(std::span<const std::uint8_t>{reinterpret_cast<const std::uint8_t*>(text.data()), text.size()});
  }

  static void write(const char* text) noexcept { write(std::string_view{text}); }

  [[nodiscard]] static auto try_read() noexcept -> int {
    const auto* const flags = reinterpret_cast<const volatile std::uint32_t*>(Base + kFlagOffset);
    if ((*flags & kRxEmpty) != 0U) {
      return -1;
    }
    return static_cast<int>(*reinterpret_cast<const volatile std::uint32_t*>(Base) & 0xFFU);
  }

  static void rx_irq_enable() noexcept {
    auto* const mask = reinterpret_cast<volatile std::uint32_t*>(Base + kMaskOffset);
    *mask            = *mask | kRxIrq;
  }
};

} // namespace nova::board::common
