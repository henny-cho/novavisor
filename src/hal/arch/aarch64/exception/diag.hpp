#pragma once

// hal/arch/aarch64/exception/diag.hpp
//
// EL2 state snapshot for fatal-trap dumps — the registers a serial log
// needs to attribute a first failure without a debugger: the stage-2
// fault IPA, the translation/vector configuration this PE was actually
// running with, and the image base that turns a raw ELR into a
// symbolizable text offset (the link base is board-specific).

#include <cstdint>

extern "C" {
// NOLINTNEXTLINE(readability-identifier-naming) — linker-script symbol
extern char __text_start[];
}

namespace nova::arch::diag {

struct El2State {
  std::uint64_t hpfar;
  std::uint64_t vbar;
  std::uint64_t sctlr;
  std::uint64_t hcr;
  std::uint64_t text_base;
};

[[nodiscard]] inline auto snapshot() noexcept -> El2State {
  El2State s{};
  __asm__ volatile("mrs %0, hpfar_el2" : "=r"(s.hpfar));
  __asm__ volatile("mrs %0, vbar_el2" : "=r"(s.vbar));
  __asm__ volatile("mrs %0, sctlr_el2" : "=r"(s.sctlr));
  __asm__ volatile("mrs %0, hcr_el2" : "=r"(s.hcr));
  s.text_base = reinterpret_cast<std::uint64_t>(__text_start);
  return s;
}

} // namespace nova::arch::diag
