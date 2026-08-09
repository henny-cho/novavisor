// components/vuart/src/vuart.cpp
//
// PL011 emulation glue: MMIO decode into the pure model, TX into the
// console mux, host RX into the focused VM plus SPI injection on the
// MIS rising edge.

#include "vuart/vuart.hpp"

#include "console_mux/console_mux.hpp"
#include "core_vcpu/core_vcpu.hpp"
#include "hal/console.hpp"
#include "hal/gic.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/guest_layout.h"
#include "nova/sync.hpp"
#include "vgic/vgic.hpp"
#include "vuart/vuart_model.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova::vuart {
namespace {

inline constexpr std::uint32_t kUartSpi         = NOVA_VUART_SPI;
inline constexpr std::uint32_t kPhysicalUartSpi = console::kRxIntid;

// Per-VM UART state. RX injection (primary core) races guest MMIO
// (owner core) — a per-VM lock covers every model mutation.
std::array<UartState, kMaxGuests>      g_uart;
std::array<sync::SpinLock, kMaxGuests> g_lock;

// Deliver the RX level as a vIRQ edge: the vGIC resolves IROUTER with
// the pending update and its reevaluate fan-out reaches the routed
// vCPU's core — no route pre-lookup or mailbox hop here.
void post_rx(std::size_t vm) noexcept {
  (void)vgic::post_spi(vm, kUartSpi);
}

// Push one host byte into a VM's RX FIFO; post on the mask-gated
// rising edge. A full FIFO drops the byte (hardware overrun shape).
void inject(std::size_t vm, std::uint8_t byte) noexcept {
  bool raise = false;
  {
    sync::Guard guard{g_lock[vm]};
    UartState&  u   = g_uart[vm];
    const bool  was = mis(u) != 0U;
    (void)rx_push(u, byte);
    raise = !was && mis(u) != 0U;
  }
  if (raise) {
    post_rx(vm);
  }
}

void log_raz_wi(std::uint64_t off) noexcept {
  console::write("[vuart] RAZ/WI offset 0x");
  console::write_hex64(off);
  console::write("\n");
}

} // namespace

void init() noexcept {
  console::rx_irq_enable();
  (void)gic::enable_spi(kPhysicalUartSpi, /*target_cpu=*/0);
  console::write("vuart: PL011 emulation active, host RX -> focus VM\n");
}

void vm_reset(std::size_t vm) noexcept {
  if (vm >= kMaxGuests) {
    return;
  }
  sync::Guard guard{g_lock[vm]};
  g_uart[vm] = UartState{};
}

} // namespace nova::vuart

namespace nova {

// Each guest's PL011 as the model holds it: what it has buffered and
// what it has unmasked.
void vuart_component::telemetry(TelemetryCall* call) noexcept {
  call->declare(&vuart::g_uart, sizeof vuart::g_uart);
}

void vuart_component::handle_mmio(MmioCall* call) noexcept {
  if (call->ipa < NOVA_VUART_IPA_BASE || call->ipa >= NOVA_VUART_IPA_BASE + vuart::kUartFrameSize) {
    return;
  }
  const std::size_t slot = vcpu::current_index();
  const std::size_t vm   = vm_of(slot);
  if (guest_table()[vm].uart != UartKind::kVuart) {
    return; // no device for this VM — the unclaimed-MMIO fault policy applies
  }
  call->handled = true;

  const std::uint64_t off   = call->ipa - NOVA_VUART_IPA_BASE;
  bool                known = false;
  bool                raise = false;
  vuart::WriteEffect  effect{};
  {
    sync::Guard       guard{vuart::g_lock[vm]};
    vuart::UartState& u = vuart::g_uart[vm];
    if (call->write) {
      const bool was = vuart::mis(u) != 0U;
      effect         = vuart::reg_write(u, off, call->value);
      known          = effect.known;
      raise          = !was && vuart::mis(u) != 0U; // IMSC unmasking a waiting level
    } else {
      const vuart::RegRead r = vuart::reg_read(u, off);
      known                  = r.known;
      call->value            = r.value;
    }
  }
  if (!known) {
    vuart::log_raz_wi(off);
    return;
  }
  if (effect.tx) {
    console_mux::guest_putc(slot, static_cast<char>(effect.tx_byte));
  }
  if (raise) {
    vuart::post_rx(vm);
  }
}

void vuart_component::handle_vm_reset(VmResetCall* call) noexcept {
  vuart::vm_reset(call->vm);
}

void vuart_component::handle_irq(IrqCall* call) noexcept {
  if (call->handled) {
    return;
  }
  if (call->intid != vuart::kPhysicalUartSpi) {
    return;
  }
  call->handled = true;
  for (int c = console::try_read(); c >= 0; c = console::try_read()) {
    const std::size_t vm = console_mux::input_route(static_cast<char>(c));
    if (vm != console_mux::kSwitched) {
      vuart::inject(vm, static_cast<std::uint8_t>(c));
    }
  }
}

} // namespace nova
