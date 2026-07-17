// components/demo_hvc/src/demo_hvc.cpp
//
// Guest HVC handlers for the demo ABI. Subscribers of HvcService see
// every HVC; we respond only to imms in 0x1000..0x10FF and silently
// return otherwise so Phase 6+/7+ components (timer, ivc) can extend
// the same service without conflict.

#include "components/demo_hvc/include/demo_hvc.hpp"

#include "components/nova_panic/include/nova_panic.hpp"
#include "hal/console.hpp"
#include "nova/trap_context.hpp"

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace nova {
namespace {

enum : std::uint16_t {
  HVC_PUTS = 0x1000,
  HVC_PUTC = 0x1001,
  HVC_EXIT = 0x1002,
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
  const auto*       data    = reinterpret_cast<const char*>(ipa);
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

void demo_hvc_component::handle_hvc(TrapContext* ctx, std::uint16_t imm) noexcept {
  switch (imm) {
  case HVC_PUTS:
    handle_puts(ctx);
    return;
  case HVC_PUTC:
    handle_putc(ctx);
    return;
  case HVC_EXIT:
    handle_exit(ctx); // [[noreturn]]
  default:
    // Outside the demo range — leave for other HvcService subscribers.
    return;
  }
}

} // namespace nova
