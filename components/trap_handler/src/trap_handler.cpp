// components/trap_handler/src/trap_handler.cpp
//
// Implements:
//   - C extern "C" entry points called from vec.S
//   - trap_handler_component::handle_lower_sync (default HVC stub)
//   - hex UART formatter for register dumps

#include "components/trap_handler/include/trap_handler.hpp"

#include "components/nova_panic/include/nova_panic.hpp"
#include "hal/board_qemu_virt/include/uart.hpp"
#include "nova/esr.hpp"
#include "nova/trap_context.hpp"

#include <array>
#include <cib/top.hpp>
#include <cstdint>
#include <span>
#include <string_view>

namespace nova {
namespace {

// ---------------------------------------------------------------------------
// Minimal hex formatter — avoids printf/itoa dependencies
// ---------------------------------------------------------------------------

constexpr std::size_t   kHexCharsPerU64 = 16; // 64 bits / 4 bits per hex digit
constexpr std::uint64_t kNibbleMask     = 0xFU;
constexpr std::size_t   kDecimalBase    = 10;

// ASCII hex digit for a nibble (0..15).
constexpr auto nibble_to_hex(std::uint8_t n) noexcept -> char {
  constexpr std::string_view digits = "0123456789abcdef";
  return digits[n];
}

// Format a 64-bit value as exactly kHexCharsPerU64 hex characters into buf
// (no NUL). buf must be at least kHexCharsPerU64 bytes.
void format_hex64(std::uint64_t v, char* buf) noexcept {
  for (std::size_t i = kHexCharsPerU64; i > 0U; --i) {
    buf[i - 1U] = nibble_to_hex(static_cast<std::uint8_t>(v & kNibbleMask));
    v >>= 4U;
  }
}

void uart_hex64(std::uint64_t v) noexcept {
  std::array<char, kHexCharsPerU64> buf{};
  format_hex64(v, buf.data());
  board::qemu_virt::uart_write(std::string_view{buf.data(), buf.size()});
}

// ---------------------------------------------------------------------------
// TrapContext register dump
// ---------------------------------------------------------------------------

void dump_trap_context(const TrapContext* ctx) noexcept {
  using board::qemu_virt::uart_write;

  uart_write("--- EL2 TRAP DUMP ---\n");

  uart_write("ESR_EL2 : 0x");
  uart_hex64(ctx->esr);
  uart_write("  FAR_EL2 : 0x");
  uart_hex64(ctx->far);
  uart_write("\n");

  uart_write("ELR_EL2 : 0x");
  uart_hex64(ctx->elr);
  uart_write("  SPSR    : 0x");
  uart_hex64(ctx->spsr);
  uart_write("\n");

  uart_write("SP_EL1  : 0x");
  uart_hex64(ctx->sp);
  uart_write("\n");

  std::size_t reg_idx = 0; // x0..x30 — at most two decimal digits
  for (const auto reg : std::span{ctx->x}) {
    uart_write("x");
    if (reg_idx < kDecimalBase) {
      const std::array<char, 1> d{static_cast<char>('0' + reg_idx)};
      uart_write(std::string_view{d.data(), 1});
    } else {
      const std::array<char, 2> d{static_cast<char>('0' + reg_idx / kDecimalBase),
                                  static_cast<char>('0' + reg_idx % kDecimalBase)};
      uart_write(std::string_view{d.data(), 2});
    }
    uart_write(" : 0x");
    uart_hex64(reg);
    uart_write("\n");
    ++reg_idx;
  }

  uart_write("---------------------\n");
}

} // namespace

// ---------------------------------------------------------------------------
// trap_handler_component::handle_lower_sync
//
// Default handler for lower-EL synchronous exceptions.
//   - HVC_AA64: log the immediate operand and advance ELR by 4 (skip HVC).
//   - All others: dump and halt.
// ---------------------------------------------------------------------------

void trap_handler_component::handle_lower_sync(TrapContext* ctx) noexcept {
  using board::qemu_virt::uart_write;
  const auto ec = esr::get_ec(ctx->esr);

  if (ec == esr::ExceptionClass::HVC_AA64) {
    // SMCCC: the function ID lives in x0; the `hvc #imm16` instruction's
    // own immediate (ESR_EL2.ISS) is conventionally 0 and is NOT the
    // function selector. Pass the low 16 bits of x0 to the service.
    //
    // ELR_EL2 already points to the instruction AFTER the HVC per
    // ARM ARM §D1.11 — do NOT advance it here or the guest will skip
    // the next instruction on return. Handlers that halt (HVC_EXIT)
    // never return through this path anyway.
    const auto func_id = static_cast<std::uint16_t>(ctx->x[0] & 0xFFFFU);
    cib::service<HvcService>(ctx, func_id);
    return;
  }

  // Unexpected synchronous exception from lower EL
  uart_write("[NOVA PANIC] Unexpected lower-EL sync exception\n");
  dump_trap_context(ctx);
  halt();
}

} // namespace nova

// ---------------------------------------------------------------------------
// extern "C" entry points — called directly from vec.S
// ---------------------------------------------------------------------------

extern "C" {

void el2_trap_lower_sync(nova::TrapContext* ctx) noexcept {
  cib::service<nova::EL2SyncTrapService>(ctx);
}

void el2_trap_current_sync(nova::TrapContext* /*ctx*/) noexcept {
  using nova::board::qemu_virt::uart_write;
  uart_write("[NOVA PANIC] EL2 self-trap (current-EL sync)\n");
  // Dump manually — CIB service may not be safe to call in this path
  // because the exception occurred inside EL2 itself.
  nova::halt();
}

void el2_trap_unhandled(nova::TrapContext* /*ctx*/) noexcept {
  using nova::board::qemu_virt::uart_write;
  uart_write("[NOVA PANIC] Unhandled EL2 exception\n");
  nova::halt();
}

} // extern "C"
