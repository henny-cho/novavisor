#pragma once

// Boot Message Component
//
// Extends cib::RuntimeStart — runs once after all initialization is
// complete, and emits the boot identity block: what this image is, what
// hardware it adopted, and what it loaded. A hardware runner cannot
// collect provenance that never reaches the console, and a first failure
// on real serial is only attributable against the state the image
// started in — so the same registers the fatal dump prints are reported
// here too (hal/diag.hpp, no separate reads).
//
// Every line leaves the UART under one lock (console::line), so a
// secondary's output cannot splice into the middle of one.
//
// The trailing banner string is an anchor the demo manifests match on;
// keep it last and keep it byte-identical.

#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/diag.hpp"
#include "hal/platform.hpp"
#include "hal/timer.hpp"
#include "nova/abi/guest.hpp"
#include "nova/arch/cpu_features.hpp"

#include <cib/top.hpp>
#include <flow/flow.hpp>
#include <string_view>

namespace nova {
namespace boot_msg_detail {

constexpr std::string_view kBanner = "NovaVisor booted\n";

// The boot VM's payload identity. Its checksum is the authoritative
// answer to "which guest image is this", and it is already validated at
// load time — reporting it makes that answer available to the runner.
inline void print_payload() noexcept {
  const std::span<const GuestDescriptor> guests = guest_table();
  if (guests.empty()) {
    console::write("[nova] payload none\n");
    return;
  }
  console::line("[nova] payload crc=", console::Hex{guests[0].payload_checksum},
                " entry=", console::Hex{guests[0].entry_pc}, " load_pa=", console::Hex{guests[0].load_pa},
                " guests=", console::Dec{guests.size()}, "\n");
}

// What the PE reports about speculation, and the verdict the guest-facing
// SMCCC answers are derived from — one decode, reported and served.
inline void print_speculation() noexcept {
  const arch::SpeculationState& s = cpu::speculation();
  console::line("[nova] cpu csv2=", console::Dec{s.csv2}, " csv2_frac=", console::Dec{s.csv2_frac},
                " csv3=", console::Dec{s.csv3}, " ssbs=", console::Dec{s.ssbs},
                " clrbhb=", console::Dec{s.clrbhb ? 1U : 0U}, "\n");
  console::line("[nova] spec branch-target=", arch::to_string(arch::branch_target_mitigation(s)),
                " store-bypass=", arch::to_string(arch::store_bypass_mitigation(s)),
                " branch-history=", arch::to_string(arch::branch_history_mitigation(s)),
                " fault-channel=", arch::to_string(arch::fault_channel_mitigation(s)), "\n");
}

inline void print_identity() noexcept {
  const diag::El2State el2 = diag::snapshot();
  console::line("[nova] image board=", platform::kBoardName, " profile=", platform::kProfileName,
                " text=", console::Hex{el2.text_base}, "\n");
  console::line("[nova] el2 sctlr=", console::Hex{el2.sctlr}, " hcr=", console::Hex{el2.hcr},
                " vbar=", console::Hex{el2.vbar}, "\n");
  console::line("[nova] boot pe mpidr=", console::Hex{cpu::affinity_of(0)}, " cores=", console::Dec{cpu::kMaxCpus},
                " cntfrq=", console::Dec{hyp_timer::freq()}, "\n");
  print_payload();
  print_speculation();
  console::write(kBanner);
}

} // namespace boot_msg_detail

struct boot_msg_component {
  constexpr static auto PRINT_BOOT_MSG = flow::action<"boot_msg">([]() noexcept { boot_msg_detail::print_identity(); });

  constexpr static auto config = cib::config(cib::extend<cib::RuntimeStart>(*PRINT_BOOT_MSG));
};

} // namespace nova
