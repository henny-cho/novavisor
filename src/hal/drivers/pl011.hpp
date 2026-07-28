#pragma once

// hal/drivers/pl011.hpp
//
// PL011 UART driver, parameterized on the register base a board
// supplies. Polled TX/RX only — the console needs no interrupts.

#include "hal/drivers/pl011_regs.h"
#include "hal/timer.hpp"

#include <cstdint>
#include <span>
#include <string_view>

namespace nova::drivers {

template <std::uintptr_t Base>
struct Pl011 {
  // Waiting for FIFO space is the one place the console can hang. A full
  // 16-deep FIFO drains in ~1.4 ms at 115200 baud and ~17 ms at 9600, so
  // the bound has to clear the slowest plausible console — while still
  // ending, because an unclocked or disabled UART never drains and the
  // alternative is a panic report that stops on its first character.
  inline static constexpr std::uint64_t kTxTimeoutUs = 20'000;

  static auto reg(std::uintptr_t offset) noexcept -> volatile std::uint32_t* {
    return reinterpret_cast<volatile std::uint32_t*>(Base + offset);
  }

  // Firmware normally hands the console over enabled, but "normally" is
  // not a contract: a UART left disabled swallows every byte, and a board
  // that swallows its own breadcrumbs is indistinguishable from a dead
  // one. Baud rate stays with firmware — that needs a board clock this
  // layer does not have — so this guarantees only that what we write
  // leaves the device.
  static void ensure_enabled() noexcept {
    constexpr std::uint32_t kNeeded = NOVA_PL011_CR_UARTEN | NOVA_PL011_CR_TXE | NOVA_PL011_CR_RXE;
    auto* const             control = reg(NOVA_PL011_CR);
    if ((*control & kNeeded) != kNeeded) {
      *control = *control | kNeeded;
    }
  }

  static void write(std::span<const std::uint8_t> data) noexcept {
    for (const std::uint8_t byte : data) {
      timer::Budget budget{kTxTimeoutUs};
      while ((*reg(NOVA_PL011_FR) & NOVA_PL011_FR_TXFF) != 0U && !budget.expired()) {
      }
      *reg(NOVA_PL011_DR) = byte;
    }
  }

  static void write(std::string_view text) noexcept {
    write(std::span<const std::uint8_t>{reinterpret_cast<const std::uint8_t*>(text.data()), text.size()});
  }

  static void write(const char* text) noexcept { write(std::string_view{text}); }

  [[nodiscard]] static auto try_read() noexcept -> int {
    if ((*reg(NOVA_PL011_FR) & NOVA_PL011_FR_RXFE) != 0U) {
      return -1;
    }
    return static_cast<int>(*reg(NOVA_PL011_DR) & 0xFFU);
  }

  static void rx_irq_enable() noexcept {
    auto* const mask = reg(NOVA_PL011_IMSC);
    *mask            = *mask | NOVA_PL011_IMSC_RX;
  }
};

} // namespace nova::drivers
