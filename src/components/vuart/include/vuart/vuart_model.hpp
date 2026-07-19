#pragma once

// components/vuart/include/vuart/vuart_model.hpp
//
// Pure PL011 register model — no bare-metal runtime dependency, fully
// host-testable. One emulated UART per VM: an RX FIFO the hypervisor
// pushes host input into, and a stateless TX (a DR write is reported
// to the caller, which drains it into the console mux synchronously —
// the TX FIFO is never full and never interrupts).
//
// Interrupt semantics are level-shaped: RIS.RX follows FIFO occupancy,
// MIS = RIS & IMSC. The component posts the UART SPI on a MIS rising
// edge; a guest drains DR until FR.RXFE to consume the level (ICR is
// accepted but cannot clear an RX level the FIFO still asserts).
//
// Reference: Arm DDI 0183 (PL011 TRM), QEMU hw/char/pl011 ID values.

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova::vuart {

inline constexpr std::uint64_t kUartFrameSize = 0x1000;

// Register offsets (DDI 0183 §3.2).
inline constexpr std::uint64_t kUartDr   = 0x000;
inline constexpr std::uint64_t kUartRsr  = 0x004;
inline constexpr std::uint64_t kUartFr   = 0x018;
inline constexpr std::uint64_t kUartIbrd = 0x024;
inline constexpr std::uint64_t kUartFbrd = 0x028;
inline constexpr std::uint64_t kUartLcrH = 0x02C;
inline constexpr std::uint64_t kUartCr   = 0x030;
inline constexpr std::uint64_t kUartIfls = 0x034;
inline constexpr std::uint64_t kUartImsc = 0x038;
inline constexpr std::uint64_t kUartRis  = 0x03C;
inline constexpr std::uint64_t kUartMis  = 0x040;
inline constexpr std::uint64_t kUartIcr  = 0x044;
inline constexpr std::uint64_t kUartIds  = 0xFE0; // PeriphID0..CellID3, 4 bytes apart

// FR bits.
inline constexpr std::uint32_t kFrRxfe = 1U << 4U;
inline constexpr std::uint32_t kFrTxff = 1U << 5U;
inline constexpr std::uint32_t kFrTxfe = 1U << 7U;

// Interrupt bit shared by IMSC/RIS/MIS/ICR.
inline constexpr std::uint32_t kIntRx = 1U << 4U;

// All maskable interrupt bits a guest may set in IMSC (DDI 0183: [10:1]).
inline constexpr std::uint32_t kImscMask = 0x7FFU;

inline constexpr std::size_t kFifoDepth = 16;

struct UartState {
  std::array<std::uint8_t, kFifoDepth> fifo{};
  std::uint8_t                         head  = 0; // next pop
  std::uint8_t                         count = 0;
  std::uint32_t                        imsc  = 0;
};

[[nodiscard]] constexpr auto rx_empty(const UartState& u) noexcept -> bool {
  return u.count == 0;
}

// False when the FIFO is full — the byte is dropped, like a hardware
// overrun (minus the RSR error bit nobody reads).
[[nodiscard]] constexpr auto rx_push(UartState& u, std::uint8_t b) noexcept -> bool {
  if (u.count == kFifoDepth) {
    return false;
  }
  u.fifo[(u.head + u.count) % kFifoDepth] = b;
  ++u.count;
  return true;
}

[[nodiscard]] constexpr auto rx_pop(UartState& u) noexcept -> std::uint8_t {
  if (u.count == 0) {
    return 0; // architecturally UNPREDICTABLE — a driver checks RXFE first
  }
  const std::uint8_t b = u.fifo[u.head];
  u.head               = (u.head + 1) % kFifoDepth;
  --u.count;
  return b;
}

// Level views. TX never raises: DR writes drain synchronously.
[[nodiscard]] constexpr auto ris(const UartState& u) noexcept -> std::uint32_t {
  return rx_empty(u) ? 0U : kIntRx;
}
[[nodiscard]] constexpr auto mis(const UartState& u) noexcept -> std::uint32_t {
  return ris(u) & u.imsc;
}

struct RegRead {
  bool          known = false;
  std::uint64_t value = 0;
};

struct WriteEffect {
  bool         known   = false;
  bool         tx      = false; // caller forwards tx_byte to the console mux
  std::uint8_t tx_byte = 0;
};

// QEMU's PL011 identification block (PeriphID0..3, CellID0..3).
inline constexpr std::array<std::uint8_t, 8> kUartIdValues{0x11, 0x10, 0x14, 0x00, 0x0D, 0xF0, 0x05, 0xB1};

[[nodiscard]] constexpr auto reg_read(UartState& u, std::uint64_t off) noexcept -> RegRead {
  if (off >= kUartIds && off < kUartFrameSize && (off % 4U) == 0U) {
    return {.known = true, .value = kUartIdValues[(off - kUartIds) / 4U]};
  }
  switch (off) {
  case kUartDr:
    return {.known = true, .value = rx_pop(u)};
  case kUartFr:
    return {.known = true, .value = kFrTxfe | (rx_empty(u) ? kFrRxfe : 0U)};
  case kUartImsc:
    return {.known = true, .value = u.imsc};
  case kUartRis:
    return {.known = true, .value = ris(u)};
  case kUartMis:
    return {.known = true, .value = mis(u)};
  case kUartRsr:
  case kUartIbrd:
  case kUartFbrd:
  case kUartLcrH:
  case kUartCr:
  case kUartIfls:
    return {.known = true, .value = 0}; // line/flow config is cosmetic here
  default:
    return {};
  }
}

[[nodiscard]] constexpr auto reg_write(UartState& u, std::uint64_t off, std::uint64_t value) noexcept -> WriteEffect {
  switch (off) {
  case kUartDr:
    return {.known = true, .tx = true, .tx_byte = static_cast<std::uint8_t>(value)};
  case kUartImsc:
    u.imsc = static_cast<std::uint32_t>(value) & kImscMask;
    return {.known = true};
  case kUartRsr:
  case kUartIbrd:
  case kUartFbrd:
  case kUartLcrH:
  case kUartCr:
  case kUartIfls:
  case kUartIcr: // RX is a level from the FIFO — draining DR is the clear
    return {.known = true};
  default:
    return {};
  }
}

} // namespace nova::vuart
