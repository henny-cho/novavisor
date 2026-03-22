#pragma once

// Idle Loop Component
//
// Extends cib::MainLoop — executed repeatedly after RuntimeStart completes.
// Issues a WFI (Wait For Interrupt) instruction to yield the CPU while idle,
// reducing power consumption in QEMU and enabling future interrupt-driven
// event handling.

#include <cib/top.hpp>
#include <flow/flow.hpp>

namespace novavisor {

struct idle_component {
  constexpr static auto WFI = flow::action<"wfi">([]() noexcept { asm volatile("wfi"); });

  constexpr static auto config = cib::config(cib::extend<cib::MainLoop>(*WFI));
};

} // namespace novavisor
