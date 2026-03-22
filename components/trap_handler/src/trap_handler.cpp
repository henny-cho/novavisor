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
#include <string_view>

namespace nova {
namespace {

// ---------------------------------------------------------------------------
// Minimal hex formatter — avoids printf/itoa dependencies
// ---------------------------------------------------------------------------

// Write a single nibble as an ASCII hex digit to buf[0]. Returns next position.
constexpr char nibble_to_hex(std::uint8_t n) noexcept {
  return static_cast<char>(n < 10U ? '0' + n : 'a' + (n - 10U));
}

// Format a 64-bit value as exactly 16 hex characters into buf (no NUL).
// buf must be at least 16 bytes.
void format_hex64(std::uint64_t v, char* buf) noexcept {
  for (int i = 15; i >= 0; --i) {
    buf[i] = nibble_to_hex(static_cast<std::uint8_t>(v & 0xFU));
    v >>= 4U;
  }
}

void uart_hex64(std::uint64_t v) noexcept {
  std::array<char, 16> buf{};
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

  for (int i = 0; i <= 30; ++i) {
    uart_write("x");
    if (i < 10) {
      std::array<char, 1> d{static_cast<char>('0' + i)};
      uart_write(std::string_view{d.data(), 1});
    } else {
      std::array<char, 2> d{static_cast<char>('0' + i / 10), static_cast<char>('0' + i % 10)};
      uart_write(std::string_view{d.data(), 2});
    }
    uart_write(" : 0x");
    uart_hex64(ctx->x[static_cast<std::size_t>(i)]);
    uart_write("\n");
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
    const auto imm = esr::get_hvc_imm(ctx->esr);
    uart_write("[HVC] imm=0x");
    uart_hex64(imm);
    uart_write(" ELR=0x");
    uart_hex64(ctx->elr);
    uart_write("\n");
    // Advance past the HVC instruction
    ctx->elr += 4U;
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
