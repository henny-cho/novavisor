#pragma once

// components/trap_handler/include/trap_handler/elr_policy.hpp
//
// Pure ELR_EL2 advance policy per exception class, host-testable. Every
// trapped class resumes the guest differently: some arrive with ELR
// already past the instruction, some must be stepped over before the
// handler runs, some must re-execute it. Getting one wrong makes the
// guest skip or repeat an instruction — a silent corruption with no
// diagnostic, so the whole matrix lives in one switch the host test
// pins class by class.
//
// Reference: Arm ARM D1.11 (exception entry, ELR_EL2 semantics).

#include "nova/arch/esr.hpp"

namespace nova::trap {

// How the router treats ELR_EL2 for a trapped class.
enum class ElrAdvance : std::uint8_t {
  kNone,           // arrives already past the instruction — leave it alone
  kBeforeDispatch, // step over the instruction before the handler runs
  kNever,          // stays at the instruction so returning re-executes it
  kOnClaim,        // stepped over only after a subscriber emulated it
  kPerHandler,     // the emulation path owns the advance
  kFault,          // not routed here — escalated as a guest fault
};

[[nodiscard]] constexpr auto elr_policy(esr::ExceptionClass ec) noexcept -> ElrAdvance {
  switch (ec) {
  case esr::ExceptionClass::HVC_AA64:
    // ELR_EL2 already points to the instruction AFTER the HVC — an
    // advance here would make the guest skip its next instruction.
    return ElrAdvance::kNone;

  // Second PSCI conduit (HCR_EL2.TSC). Unlike HVC, ELR points AT the
  // trapped smc — step over it before the shared HVC fan-out.
  case esr::ExceptionClass::SMC_AA64:
  // Step over the wfi/wfe so the guest resumes after it, whether the
  // handler returns immediately or parks the vCPU first.
  case esr::ExceptionClass::WFx:
    return ElrAdvance::kBeforeDispatch;

  case esr::ExceptionClass::FP_SIMD:
    // Once the handler has made FP access legal, returning re-executes
    // the trapped instruction successfully.
    return ElrAdvance::kNever;

  case esr::ExceptionClass::MSR_MRS:
    // Advance only after successful emulation, so fault diagnostics
    // retain the offending MSR/MRS.
    return ElrAdvance::kOnClaim;

  case esr::ExceptionClass::DATA_ABORT_LOWER:
    // The MMIO decode knows the instruction length and whether the
    // access was emulated at all — it advances, or faults the guest.
    return ElrAdvance::kPerHandler;

  default:
    // Unrouted: the class either belongs to the guest (isolated through
    // GuestFaultService, which never resumes the instruction) or claims
    // an EL2 origin the router panics on.
    return ElrAdvance::kFault;
  }
}

} // namespace nova::trap
