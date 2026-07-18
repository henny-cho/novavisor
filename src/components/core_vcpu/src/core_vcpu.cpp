// components/core_vcpu/src/core_vcpu.cpp
//
// Cooperative VCPU scheduler. A switch swaps the live EL2 trap frame
// with the target's saved TrapContext, moves the EL1 sysreg bank
// (hal/arch/aarch64/el1_context.hpp) and the vGIC CPU-interface state,
// and retargets VTTBR_EL2 — the common vec.S restore path then resumes
// the new guest.

#include "core_vcpu/core_vcpu.hpp"

#include "core_mmu/core_mmu.hpp"
#include "hal/console.hpp"
#include "nova/abi/guest.hpp"
#include "nova/abi/hvc_abi.h"
#include "nova_panic/nova_panic.hpp"
#include "vgic/vgic.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova {

// Defined in hal/arch/aarch64/vcpu_enter.S.
extern "C" [[noreturn]] void nova_vcpu_enter(std::uint64_t entry_pc, std::uint64_t sp_el1,
                                             std::uint64_t spsr_el2) noexcept;

namespace vcpu {
namespace {

// SPSR_EL2 value to restore on ERET into a fresh guest:
//   M[3:0]   = 0b0101  EL1h  (EL1, using SP_EL1)
//   M[4]     = 0       AArch64 execution state
//   F (b6)   = 1       FIQ masked
//   I (b7)   = 1       IRQ masked
//   A (b8)   = 1       SError masked
//   D (b9)   = 1       Debug masked
//   others   = 0
// The guest unmasks I itself once its vector table is installed.
inline constexpr std::uint64_t kSpsrEl1h = 0x3C5ULL;

std::array<Vcpu, kMaxGuests> g_vcpus;
std::size_t                  g_count   = 0;
std::size_t                  g_current = 0;

// Reset a VCPU to its descriptor's boot state. GP registers are zeroed
// so no EL2 (or previous-guest) values leak into a fresh guest.
void seed(std::size_t index, const GuestDescriptor& guest) noexcept {
  Vcpu& v    = g_vcpus[index];
  v.guest    = &guest;
  v.ctx      = TrapContext{};
  v.ctx.elr  = guest.entry_pc;
  v.ctx.sp   = guest.stack_top;
  v.ctx.spsr = kSpsrEl1h;
  v.el1      = arch::El1SysregBank{};
  vgic::cpu_reset(index);
}

// Next kReady VCPU after g_current in ring order; g_count when none.
auto pick_next() noexcept -> std::size_t {
  for (std::size_t step = 1; step <= g_count; ++step) {
    const std::size_t idx = (g_current + step) % g_count;
    if (g_vcpus[idx].state == Vcpu::State::kReady) {
      return idx;
    }
  }
  return g_count;
}

// Swap the resident VCPU: park the outgoing guest's state (trap frame,
// EL1 bank, vGIC CPU interface), load the incoming one, retarget
// Stage 2. The caller returns through vec.S which restores *live — now
// the new guest.
void switch_to(TrapContext* live, std::size_t next_idx) noexcept {
  Vcpu& cur  = g_vcpus[g_current];
  Vcpu& next = g_vcpus[next_idx];

  cur.ctx = *live;
  cur.el1 = arch::read_el1_bank();
  vgic::cpu_save(g_current);
  if (cur.state == Vcpu::State::kRunning) { // exit_current parks it as kOff
    cur.state = Vcpu::State::kReady;
  }

  *live = next.ctx;
  arch::write_el1_bank(next.el1);
  vgic::cpu_restore(next_idx);
  mmu::switch_vm(next_idx);

  next.state = Vcpu::State::kRunning;
  g_current  = next_idx;
}

} // namespace

auto current() noexcept -> Vcpu& {
  return g_vcpus[g_current];
}

auto current_index() noexcept -> std::size_t {
  return g_current;
}

void init() noexcept {
  const auto guests = guest_table();
  g_count           = guests.size() <= kMaxGuests ? guests.size() : kMaxGuests; // core_mmu already panicked if over
  for (std::size_t i = 0; i < g_count; ++i) {
    seed(i, guests[i]);
  }
  g_vcpus[0].state = Vcpu::State::kReady;
}

[[noreturn]] void enter_guest() noexcept {
  Vcpu& boot = g_vcpus[0];
  boot.state = Vcpu::State::kRunning;
  g_current  = 0;
  nova_vcpu_enter(boot.ctx.elr, boot.ctx.sp, boot.ctx.spsr);
}

void yield_current(TrapContext* live) noexcept {
  const std::size_t next = pick_next();
  if (next < g_count) {
    switch_to(live, next);
  }
}

auto start_vm(std::size_t index) noexcept -> bool {
  if (index >= g_count || g_vcpus[index].state != Vcpu::State::kOff) {
    return false;
  }
  seed(index, guest_table()[index]);
  g_vcpus[index].state = Vcpu::State::kReady;
  return true;
}

void exit_current(TrapContext* live) noexcept {
  g_vcpus[g_current].state = Vcpu::State::kOff;

  const std::size_t next = pick_next();
  if (next >= g_count) {
    console::write("[core_vcpu] all VCPUs off — halting\n");
    halt();
  }
  switch_to(live, next);
}

auto post_virq(std::size_t index, std::uint32_t vintid) noexcept -> bool {
  if (index >= g_count || g_vcpus[index].state == Vcpu::State::kOff) {
    return false;
  }
  return vgic::post(index, vintid);
}

} // namespace vcpu

void core_vcpu_component::handle_guest_fault(GuestFaultCall* call) noexcept {
  call->handled = true;
  console::write("[core_vcpu] guest fault — stopping VCPU ");
  console::write_dec64(vcpu::current_index());
  console::write("\n");
  vcpu::exit_current(call->ctx);
}

void core_vcpu_component::handle_hvc(HvcCall* call) noexcept {
  switch (call->func_id) {
  case NOVA_HVC_FN_YIELD:
    call->handled   = true;
    call->ctx->x[0] = 0; // written before the frame swap parks it
    vcpu::yield_current(call->ctx);
    return;
  case NOVA_HVC_FN_VM_START:
    call->handled   = true;
    call->ctx->x[0] = vcpu::start_vm(static_cast<std::size_t>(call->ctx->x[1])) ? 0 : kSmcccNotSupported;
    return;
  default:
    return; // not ours — leave unclaimed for other subscribers
  }
}

} // namespace nova
