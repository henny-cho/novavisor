// components/demo_hvc/src/demo_hvc.cpp
//
// Guest HVC handlers for the demo ABI. Subscribers of HvcService see
// every HVC; we claim only the demo function IDs and silently return
// otherwise so Phase 6+/7+ components (timer, ivc) can extend the
// same service without conflict.

#include "demo_hvc/demo_hvc.hpp"

#include "console_mux/console_mux.hpp"
#include "core_vcpu/core_vcpu.hpp"
#include "hal/console.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/hvc_abi.h"
#include "nova/arch/trap_context.hpp"
#include "smp/smp.hpp"

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace nova {
namespace {

// Function IDs from the ABI header shared with the guest-side stubs.
enum : std::uint16_t {
  kHvcPuts         = NOVA_HVC_FN_PUTS,
  kHvcPutc         = NOVA_HVC_FN_PUTC,
  kHvcExit         = NOVA_HVC_FN_EXIT,
  kHvcDiagEl2Fault = NOVA_HVC_FN_DIAG_EL2_FAULT,
};

// Upper bound on bytes we will copy out of guest memory for kHvcPuts.
// Guards against a runaway guest request; real apps should fit well
// under this.
constexpr std::size_t kMaxPutsLen = 1024;

// kHvcPuts: x1 = guest IPA of byte buffer, x2 = length.
// The IPA is translated to its backing PA through the calling guest's
// descriptor (EL2 runs with a flat physical view). Phase 8+ MMIO-trap
// guests will need a richer IPA-to-EL2-VA translation helper.
void handle_puts(TrapContext* ctx) noexcept {
  const auto        ipa     = ctx->x[1];
  const auto        req_len = ctx->x[2];
  const std::size_t len     = (req_len > kMaxPutsLen) ? kMaxPutsLen : static_cast<std::size_t>(req_len);

  // Reject buffers that are not fully inside the guest IPA window —
  // otherwise a guest could point x1 at hypervisor memory and leak EL2
  // contents through the UART.
  const GuestDescriptor& guest = *vcpu::current().guest;
  if (!guest.contains(ipa, len)) {
    console::write("[demo_hvc] PUTS rejected: buffer outside guest window\n");
    return;
  }

  const auto* data = reinterpret_cast<const char*>(guest.to_pa(ipa));
  console_mux::guest_write(vcpu::current_index(), std::string_view{data, len});
}

void handle_putc(TrapContext* ctx) noexcept {
  console_mux::guest_putc(vcpu::current_index(), static_cast<char>(ctx->x[1] & 0xFFU));
}

// kHvcExit: x1 = exit code. Emits the manifest-expected "demo_exit
// code=N" line, then retires the whole VM through its owner lifecycle.
// The line stays untagged (harness contract); a buffered partial line
// is flushed first so nothing the guest printed is lost.
void handle_exit(TrapContext* ctx) noexcept {
  console_mux::flush(vcpu::current_index());
  // One atomic line: the verification harness greps for it, so another
  // core's log must never splice into it.
  console::line("demo_exit code=", console::Dec{ctx->x[1]}, "\n");
  smp::stop_vm(vm_of(vcpu::current_index()), ctx);
}

// kHvcDiagEl2Fault: fault EL2 on purpose — a store into EL2's own
// .rodata takes a W^X permission fault through the el2h_sync vector,
// exercising the fatal/panic/dump path end-to-end (demo 18). The
// object's address is taken so storage is emitted into .rodata.
void handle_diag_el2_fault() noexcept {
  static constexpr std::uint64_t kRoProbe = 0x600D;
  // NOLINTNEXTLINE(cppcoreguidelines-pro-type-const-cast) — the fault is the point
  auto* probe = const_cast<volatile std::uint64_t*>(reinterpret_cast<const volatile std::uint64_t*>(&kRoProbe));
  *probe      = 0; // permission fault → EL2 fatal vector
}

} // namespace

void demo_hvc_component::handle_hvc(HvcCall* call) noexcept {
  if (call->handled) {
    return;
  }
  switch (call->func_id) {
  case kHvcPuts:
    call->handled = true;
    handle_puts(call->ctx);
    return;
  case kHvcPutc:
    call->handled = true;
    handle_putc(call->ctx);
    return;
  case kHvcExit:
    call->handled = true;
    handle_exit(call->ctx);
    return;
  case kHvcDiagEl2Fault:
    call->handled = true;
    handle_diag_el2_fault();
    return;
  default:
    // Not ours — leave unclaimed for other HvcService subscribers.
    return;
  }
}

} // namespace nova
