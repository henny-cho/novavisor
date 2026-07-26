// hal/arch/aarch64/vmsa/el2_mmu.cpp
//
// EL2 Stage-1 identity map construction and activation.
//
// EL2 previously ran with its own MMU off, which makes every EL2
// access Device-nGnRnE: exclusives (locks, atomics) lose their
// architectural guarantee, EL2's uncached writes race guests' cached
// views of the same RAM, and unaligned accesses fault. QEMU models
// none of this — real silicon does. The map here turns EL2 into an
// ordinary cacheable, W^X-enforced translation regime:
//
//   [0, ram_lo)                  Device-nGnRE   all board MMIO
//   [ram_lo, __text_start)       Normal RW XN
//   [__text_start, __text_end)   Normal RO exec .text (+ vectors)
//   [__text_end, __rodata_end)   Normal RO XN   .rodata
//   [__rodata_end, ram_hi)       Normal RW XN   data/bss/stacks/guest RAM
//
// The primary builds the tables with the MMU still off; those writes
// land in RAM directly, and boot.S invalidated the caches at entry, so
// the cacheable table walk that follows sees exactly what was written.
// Secondaries never read tables or globals before their own enable:
// boot.S passes the root symbol and register constants directly to
// nova_el2_stage1_enable, so no pre-MMU stack or data access exists on
// that path at all.

#include "hal/arch/aarch64/vmsa/stage1_tables.hpp"
#include "hal/board/active/board_layout.h"
#include "hal/board/active/uart.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <span>

namespace {

namespace stage1 = nova::arch::stage1;

constexpr std::uint64_t kRamLo = std::min<std::uint64_t>(NOVA_BOARD_PHYS_RAM_BASE, NOVA_BOARD_RAM_BASE);
constexpr std::uint64_t kRamHi =
    std::max<std::uint64_t>(NOVA_BOARD_PHYS_RAM_BASE + std::uint64_t{NOVA_BOARD_PHYS_RAM_SIZE},
                            NOVA_BOARD_RAM_BASE + std::uint64_t{NOVA_BOARD_RAM_SIZE});

// T0SZ=32 walk: everything EL2 touches must sit below 4 GiB, and every
// MMIO base must fall inside the single low device window.
static_assert(kRamHi <= stage1::kVaLimit);
static_assert(NOVA_BOARD_UART0_BASE < kRamLo);
static_assert(NOVA_BOARD_GICD_BASE < kRamLo);
static_assert(NOVA_BOARD_GICR_BASE < kRamLo);
static_assert(NOVA_BOARD_SMMU_BASE + std::uint64_t{NOVA_BOARD_SMMU_SIZE} <= kRamLo);
#ifdef NOVA_BOARD_PCIE_ECAM_BASE
static_assert(NOVA_BOARD_PCIE_ECAM_BASE < kRamLo);
static_assert(NOVA_BOARD_PCIE_MMIO_BASE < kRamLo);
#endif

// Worst case today is one L2 (partial-GiB RAM) plus a few L3 tables
// around the page-aligned section boundaries; eight leaves headroom.
constexpr std::size_t kPoolTables = 8;
alignas(4096) std::array<stage1::Table, kPoolTables> g_pool;

} // namespace

extern "C" {

// Section boundaries the linker script page-aligns for W^X.
// NOLINTBEGIN(readability-identifier-naming) — linker-script symbols
extern char __text_start[];
extern char __text_end[];
extern char __rodata_end[];
extern char __stack_top[];
// NOLINTEND(readability-identifier-naming)

// mmu.S. Loads MAIR/TCR/TTBR0, invalidates EL2 TLB + I-cache, then
// writes SCTLR_EL2 as a whole. Leaf and stack-free by contract: the
// secondary entry calls it before it has a usable stack.
void nova_el2_stage1_enable(std::uint64_t ttbr0, std::uint64_t tcr, std::uint64_t mair, std::uint64_t sctlr) noexcept;

// Root L1 table. Named C-linkage so boot.S can pass its address on the
// secondary path without touching any runtime-initialized state.
alignas(4096) stage1::Table nova_el2_l1_root; // NOLINT(readability-identifier-naming)

// Primary boot path (boot.S, after BSS zeroing): build the identity
// map, then enable this core's translation regime. The per-core stack
// guard pages stay unmapped so an EL2 stack overflow faults instead of
// silently corrupting the neighbor stack or .bss.
void nova_el2_mmu_init() noexcept {
  stage1::Stage1Builder builder{nova_el2_l1_root, std::span{g_pool}};

  const auto text_start = reinterpret_cast<std::uintptr_t>(__text_start);
  const auto text_end   = reinterpret_cast<std::uintptr_t>(__text_end);
  const auto rodata_end = reinterpret_cast<std::uintptr_t>(__rodata_end);
  const auto stack_top  = reinterpret_cast<std::uintptr_t>(__stack_top);

  bool ok = builder.map(0, kRamLo, stage1::desc::kAttrDevice) &&
            builder.map(kRamLo, text_start, stage1::desc::kAttrNormalRw) &&
            builder.map(text_start, text_end, stage1::desc::kAttrNormalRx) &&
            builder.map(text_end, rodata_end, stage1::desc::kAttrNormalRo);

  // RW tail in fragments, skipping each stack's guard page (linker.ld.S
  // reserves SIZE + 4 KiB per core, guard below the stack).
  constexpr std::uintptr_t kGuardSize = 0x1000;
  constexpr std::uintptr_t kStride    = NOVA_BOARD_EL2_STACK_SIZE + kGuardSize;
  std::uintptr_t           cursor     = rodata_end;
  for (std::size_t k = NOVA_BOARD_SMP_CPUS; k >= 1; --k) {
    const std::uintptr_t guard = stack_top - (k * kStride);
    ok                         = ok && builder.map(cursor, guard, stage1::desc::kAttrNormalRw);
    cursor                     = guard + kGuardSize;
  }
  ok = ok && builder.map(cursor, kRamHi, stage1::desc::kAttrNormalRw);

  if (!ok) {
    // Pre-console: the shared console does not exist yet, but the
    // board UART does — leave a breadcrumb so this park is
    // distinguishable from a dead board, then stop instead of running
    // uncached forever. The geometry is static per board, so reaching
    // this is a build-time layout bug.
    nova::board::active::Uart::write("[boot] halt: EL2 stage-1 map build failed\n");
    for (;;) {
      __asm__ volatile("wfi");
    }
  }

  nova_el2_stage1_enable(reinterpret_cast<std::uintptr_t>(&nova_el2_l1_root), stage1::kTcrEl2, stage1::kMairEl2,
                         stage1::kSctlrEl2);
}

} // extern "C"
