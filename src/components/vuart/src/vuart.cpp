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
#include "smp/smp.hpp"
#include "vgic/vgic.hpp"
#include "vuart/vuart_model.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova::vuart {
namespace {

inline constexpr std::uint32_t kUartSpi = NOVA_VUART_SPI;

// Per-VM UART state. RX injection (primary core) races guest MMIO
// (owner core) — the lock covers every model mutation.
std::array<UartState, kMaxGuests> g_uart;
sync::SpinLock                    g_lock;

// Deliver the RX level as a vIRQ edge: IROUTER picks the vCPU, smp
// carries it across cores when the target lives elsewhere.
void post_rx(std::size_t vm) noexcept {
  const std::size_t target = vgic::spi_target_vcpu(vm, kUartSpi);
  (void)smp::post_virq(slot_of(vm, target), kUartSpi);
}

// Push one host byte into a VM's RX FIFO; post on the mask-gated
// rising edge. A full FIFO drops the byte (hardware overrun shape).
void inject(std::size_t vm, std::uint8_t byte) noexcept {
  bool raise = false;
  {
    sync::Guard guard{g_lock};
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
  gic::enable_spi(kUartSpi, /*target_cpu=*/0); // one input consumer: the primary core
  console::write("vuart: PL011 emulation active, host RX -> focus VM\n");
}

} // namespace nova::vuart

namespace nova {

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
    sync::Guard       guard{vuart::g_lock};
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

void vuart_component::handle_irq(IrqCall* call) noexcept {
  if (call->intid != vuart::kUartSpi) {
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
