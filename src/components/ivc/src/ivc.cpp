// components/ivc/src/ivc.cpp

#include "ivc/ivc.hpp"

#include "core_vcpu/core_vcpu.hpp"
#include "nova/abi/hvc_abi.h"
#include "nova/arch/trap_context.hpp"

#include <cstddef>

namespace nova {

void ivc_component::handle_hvc(HvcCall* call) noexcept {
  if (call->func_id != NOVA_HVC_FN_IVC_DOORBELL) {
    return; // not ours — leave unclaimed for other subscribers
  }
  call->handled = true;

  // Self-ringing is allowed (the pending vIRQ is taken right after
  // ERET); posting to an off or out-of-range VM is the caller's error.
  const auto target = static_cast<std::size_t>(call->ctx->x[1]);
  call->ctx->x[0]   = vcpu::post_virq(target, NOVA_IVC_DOORBELL_VINTID) ? 0 : kSmcccNotSupported;
}

} // namespace nova
