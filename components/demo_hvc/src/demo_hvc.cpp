// components/demo_hvc/src/demo_hvc.cpp
//
// Guest HVC handlers for the demo ABI. Subscribers of HvcService see
// every HVC; we claim only the demo function IDs and silently return
// otherwise so Phase 6+/7+ components (timer, ivc) can extend the
// same service without conflict.

#include "components/demo_hvc/include/demo_hvc.hpp"

#include "components/nova_panic/include/nova_panic.hpp"
#include "hal/console.hpp"
#include "nova/guest_layout.h"
#include "nova/hvc_abi.h"
#include "nova/trap_context.hpp"

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace nova {
namespace {

// Function IDs from the ABI header shared with the guest-side stubs.
enum : std::uint16_t {
  HVC_PUTS = NOVA_HVC_FN_PUTS,
  HVC_PUTC = NOVA_HVC_FN_PUTC,
  HVC_EXIT = NOVA_HVC_FN_EXIT,
};

// Upper bound on bytes we will copy out of guest memory for HVC_PUTS.
// Guards against a runaway guest request; real apps should fit well
// under this.
constexpr std::size_t kMaxPutsLen = 1024;

// HVC_PUTS: x1 = guest IPA of byte buffer, x2 = length.
// Phase 5 guest IPA space is identity-mapped to PA, so we dereference
// the IPA directly from EL2. Phase 8+ MMIO-trap guests will need an
// IPA-to-EL2-VA translation helper.
void handle_puts(TrapContext* ctx) noexcept {
  const auto        ipa     = ctx->x[1];
  const auto        req_len = ctx->x[2];
  const std::size_t len     = (req_len > kMaxPutsLen) ? kMaxPutsLen : static_cast<std::size_t>(req_len);

  // Reject buffers that are not fully inside the guest IPA window —
  // otherwise a guest could point x1 at hypervisor memory and leak EL2
  // contents through the UART. (len <= kMaxPutsLen <= window size, so
  // kWinEnd - len cannot underflow.)
  constexpr std::uint64_t kWinBase = NOVA_GUEST_IPA_BASE;
  constexpr std::uint64_t kWinEnd  = kWinBase + NOVA_GUEST_IPA_SIZE;
  if (ipa < kWinBase || ipa > kWinEnd - len) {
    console::write("[demo_hvc] PUTS rejected: buffer outside guest window\n");
    return;
  }

  const auto* data = reinterpret_cast<const char*>(ipa);
  console::write(std::string_view{data, len});
}

void handle_putc(TrapContext* ctx) noexcept {
  const char c = static_cast<char>(ctx->x[1] & 0xFFU);
  console::write(std::string_view{&c, 1});
}

// HVC_EXIT: x1 = exit code. Emits the manifest-expected "demo_exit
// code=N" line and halts — Phase 5 is single-guest, so guest exit
// terminates the whole hypervisor.
[[noreturn]] void handle_exit(TrapContext* ctx) noexcept {
  console::write("demo_exit code=");
  console::write_dec64(ctx->x[1]);
  console::write("\n");
  halt();
}

} // namespace

void demo_hvc_component::handle_hvc(HvcCall* call) noexcept {
  switch (call->func_id) {
  case HVC_PUTS:
    call->handled = true;
    handle_puts(call->ctx);
    return;
  case HVC_PUTC:
    call->handled = true;
    handle_putc(call->ctx);
    return;
  case HVC_EXIT:
    handle_exit(call->ctx); // [[noreturn]]
  default:
    // Not ours — leave unclaimed for other HvcService subscribers.
    return;
  }
}

} // namespace nova
