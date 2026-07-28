#pragma once

// hal/drivers/gicv3.hpp
//
// GICv3 distributor and redistributor driver, parameterized on
// the frame bases and affinity table a board supplies. Boards
// bind it in their active/gicv3.hpp facade.

#include "hal/arch/aarch64/cpu.hpp"
#include "hal/timer.hpp"
#include "nova/arch/gicv3/regs.h"
#include "nova/arch/gicv3/spi.hpp"
#include "nova/sync.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace nova::drivers {

template <typename Config>
struct Gicv3 {
  static auto mmio32(std::uintptr_t address) noexcept -> volatile std::uint32_t* {
    return reinterpret_cast<volatile std::uint32_t*>(address);
  }

  // Register-write-pending polls carry a budget: on silicon RWP takes
  // real time (QEMU reports it always clear), and an unresponsive
  // distributor must surface as a diagnosable failure rather than a
  // silent hang inside an IRQ handler. The budget is time, not
  // iterations — a loop count is unrelated to the distributor's clock.
  static constexpr std::uint64_t kRwpTimeoutUs = 10'000;

  // The redistributor wake is the slower handshake of the two: it can
  // involve a power domain coming up, not just a register settling.
  static constexpr std::uint64_t kWakeTimeoutUs = 100'000;

  static auto wait_for_rwp() noexcept -> bool {
    timer::Budget budget{kRwpTimeoutUs};
    for (;;) { // read before judging the budget: an already-clear RWP must not cost a poll
      if ((*mmio32(Config::kDistributorBase + NOVA_GICD_CTLR) & NOVA_GICD_CTLR_RWP) == 0U) {
        return true;
      }
      if (budget.expired()) {
        return false;
      }
    }
  }

  static auto wait_for_redist_rwp(std::uintptr_t frame) noexcept -> bool {
    timer::Budget budget{kRwpTimeoutUs};
    for (;;) {
      if ((*mmio32(frame + NOVA_GICR_CTLR) & NOVA_GICR_CTLR_RWP) == 0U) {
        return true;
      }
      if (budget.expired()) {
        return false;
      }
    }
  }

  // GICR_TYPER packs Aff3 into bits 31:24 of the high word, while the
  // MPIDR/IROUTER representation keeps Aff3 at bits 39:32.
  static constexpr auto typer_affinity(std::uint64_t affinity) noexcept -> std::uint32_t {
    return static_cast<std::uint32_t>(((affinity >> 32U) & 0xFFU) << 24U | (affinity & 0x00FFFFFFU));
  }

  // Walk until GICR_TYPER.Last, not until the hypervisor's CPU count:
  // a build for a subset of PEs still has to find its own frame in a
  // distributor populated for all of them, and the stride is
  // TYPER.VLPIS-dependent (GICv4 appends VLPI frames). Returns 0 when
  // this PE has no redistributor — the caller reports it, since a trap
  // here would land before the console exists.
  static auto find_redistributor_frame() noexcept -> std::uintptr_t {
    const std::uint32_t affinity = typer_affinity(arch::cpu_affinity());

    std::uintptr_t frame = Config::kRedistributorBase;
    for (;;) {
      const std::uint32_t typer_lo = *mmio32(frame + NOVA_GICR_TYPER);
      const std::uint32_t typer_hi = *mmio32(frame + NOVA_GICR_TYPER_HI);
      if (typer_hi == affinity) {
        return frame;
      }
      if ((typer_lo & NOVA_GICR_TYPER_LAST) != 0U) {
        return 0;
      }
      frame += arch::gicv3::redistributor_stride(typer_lo);
    }
  }

  // The frame assignment is fixed hardware — resolve the MMIO walk once
  // per core and serve later calls (enable_ppi runs several times per
  // core during bring-up) from the cache. Frame 0 is never a valid
  // redistributor base, so zero marks "unresolved".
  static auto redistributor_frame() noexcept -> std::uintptr_t {
    static std::array<std::uintptr_t, Config::kCpuAffinity.size()> frames{};

    std::uintptr_t& cached = frames[arch::core_index()];
    if (cached == 0U) {
      cached = find_redistributor_frame(); // stays 0 when this PE has none
    }
    return cached;
  }

  // Scrub inherited distributor state before enabling anything. On QEMU
  // the model is always at reset; after TF-A/UEFI (or a hypervisor
  // restart) leftover enables and pending/active bits would arrive as
  // interrupts nobody claims — a still-asserted level SPI turns the
  // drain loop into a livelock. Bank 0 (SGI/PPI) belongs to the
  // redistributor and is scrubbed there.
  static void scrub_distributor() noexcept {
    const std::uint32_t banks = arch::gicv3::implemented_banks(distributor_typer());
    for (std::uint32_t bank = 1; bank < banks; ++bank) {
      const std::uint32_t offset                                       = bank * arch::gicv3::kBankStride;
      *mmio32(Config::kDistributorBase + NOVA_GICD_ICENABLER + offset) = ~0U;
      *mmio32(Config::kDistributorBase + NOVA_GICD_ICPENDR + offset)   = ~0U;
      *mmio32(Config::kDistributorBase + NOVA_GICD_ICACTIVER + offset) = ~0U;
    }
    wait_for_rwp();
  }

  // True when the distributor presents a single security state: with
  // DS=0 the Non-secure EL2 view of IGROUPR/IGRPMODR is RAZ/WI for
  // INTIDs the secure world owns, so grouping is the firmware's job and
  // this driver must not assume its writes took effect.
  static auto single_security_state() noexcept -> bool {
    return (*mmio32(Config::kDistributorBase + NOVA_GICD_CTLR) & NOVA_GICD_CTLR_DS) != 0U;
  }

  static void distributor_init() noexcept {
    // Enable affinity routing first (ARE is a prerequisite for the
    // IROUTER-based routing every SPI uses), scrub, then enable Group 1.
    // Both writes preserve nothing on purpose: the scrub between them
    // means no interrupt can be delivered from inherited state.
    *mmio32(Config::kDistributorBase + NOVA_GICD_CTLR) = NOVA_GICD_CTLR_ARE;
    wait_for_rwp();
    scrub_distributor();
    *mmio32(Config::kDistributorBase + NOVA_GICD_CTLR) = NOVA_GICD_CTLR_ARE | NOVA_GICD_CTLR_ENABLE_GRP1;
    wait_for_rwp();
    __asm__ volatile("dsb sy" ::: "memory");
  }

  // Wake this PE's redistributor and scrub its private-interrupt bank.
  // Returns false when the frame is missing or the wake handshake does
  // not complete — with DS=0 GICR_WAKER is a secure register and reads
  // RAZ, so a false negative is impossible but a real hang is caught.
  static auto redistributor_init() noexcept -> bool {
    const std::uintptr_t frame = redistributor_frame();
    if (frame == 0U) {
      return false;
    }
    auto* const waker = mmio32(frame + NOVA_GICR_WAKER);
    *waker            = *waker & ~NOVA_GICR_WAKER_PROCESSOR_SLEEP;
    timer::Budget budget{kWakeTimeoutUs};
    bool          awake = false;
    for (;;) {
      awake = (*waker & NOVA_GICR_WAKER_CHILDREN_ASLEEP) == 0U;
      if (awake || budget.expired()) {
        break;
      }
    }
    if (!awake) {
      return false;
    }

    *mmio32(frame + NOVA_GICR_ICENABLER0) = ~0U; // inherited SGI/PPI state
    *mmio32(frame + NOVA_GICR_ICPENDR0)   = ~0U;
    *mmio32(frame + NOVA_GICR_ICACTIVER0) = ~0U;
    *mmio32(frame + NOVA_GICR_IGROUPR0)   = ~0U; // Non-secure Group 1 (RAZ/WI when DS=0)
    __asm__ volatile("dsb sy" ::: "memory");
    return wait_for_redist_rwp(frame);
  }

  static void enable_ppi(std::uint32_t intid) noexcept {
    const std::uintptr_t frame = redistributor_frame();
    if (frame == 0U) {
      return;
    }
    *mmio32(frame + NOVA_GICR_ISENABLER0) = 1U << intid;
    __asm__ volatile("dsb sy" ::: "memory"); // callers rely on delivery right after
  }

  // GICD_TYPER is boot-constant hardware identification — read it once
  // instead of on every SPI operation (the level-SPI rearm path issues
  // two per guest EOI). Benign if two cores race the first read.
  static auto distributor_typer() noexcept -> std::uint32_t {
    static std::uint32_t typer = 0;
    if (typer == 0U) {
      typer = *mmio32(Config::kDistributorBase + NOVA_GICD_TYPER);
    }
    return typer;
  }

  // Validated register view for one SPI; `valid` is false when the
  // INTID is out of range or unimplemented on this distributor.
  static auto resolve_spi(std::uint32_t intid) noexcept -> arch::gicv3::SpiRegisters {
    const arch::gicv3::SpiRegisters registers = arch::gicv3::spi_registers(intid);
    if (!registers.valid || !arch::gicv3::spi_implemented(intid, distributor_typer())) {
      return {};
    }
    return registers;
  }

  // Serializes the read-modify-write of the shared GICD words: IGROUPR
  // and IGRPMODR each cover 32 INTIDs and ICFGR 16, so two cores
  // configuring unrelated SPIs in the same word would lose one update.
  // Concurrency is real — device assignment reconfigures SPIs from
  // whichever core owns the VM.
  static auto config_lock() noexcept -> sync::SpinLock& {
    static sync::SpinLock lock;
    return lock;
  }

  static auto configure_spi(std::uint32_t intid, std::uint32_t core, arch::gicv3::SpiTrigger trigger) noexcept -> bool {
    const arch::gicv3::SpiRegisters registers = resolve_spi(intid);
    if (!registers.valid || core >= Config::kCpuAffinity.size()) {
      return false;
    }
    sync::Guard guard{config_lock()};

    *mmio32(Config::kDistributorBase + registers.disable_offset) = registers.bit;
    wait_for_rwp();

    // Non-secure Group 1 = IGROUPR set, IGRPMODR clear. Both are RAZ/WI
    // for secure-owned INTIDs when DS=0, so verify the group actually
    // took and refuse the SPI otherwise — silently returning success
    // would leave the interrupt going to EL3 as an FIQ we never see.
    *mmio32(Config::kDistributorBase + registers.group_offset) |= registers.bit;
    auto* const grpmod = mmio32(Config::kDistributorBase + registers.grpmod_offset);
    *grpmod            = *grpmod & ~registers.bit;
    const bool grouped = (*mmio32(Config::kDistributorBase + registers.group_offset) & registers.bit) != 0U &&
                         (*grpmod & registers.bit) == 0U;
    if (!grouped) {
      return false; // firmware owns this INTID's security state
    }

    *reinterpret_cast<volatile std::uint8_t*>(Config::kDistributorBase + NOVA_GICD_IPRIORITYR + intid) =
        arch::gicv3::kDefaultPriority;

    auto* const         config = mmio32(Config::kDistributorBase + registers.config_offset);
    const std::uint32_t edge   = trigger == arch::gicv3::SpiTrigger::kEdge ? registers.edge_bit : 0U;
    *config                    = (*config & ~registers.edge_bit) | edge;

    *reinterpret_cast<volatile std::uint64_t*>(Config::kDistributorBase + registers.route_offset) =
        Config::kCpuAffinity[core];
    __asm__ volatile("dsb sy" ::: "memory");
    return true;
  }

  // Stop an INTID nobody claims from re-arriving: disable it and clear
  // any pending/active state it left behind.
  static auto quarantine_spi(std::uint32_t intid) noexcept -> bool {
    const arch::gicv3::SpiRegisters registers = resolve_spi(intid);
    if (!registers.valid) {
      return false;
    }
    *mmio32(Config::kDistributorBase + registers.disable_offset)  = registers.bit;
    *mmio32(Config::kDistributorBase + registers.clear_offset)    = registers.bit;
    *mmio32(Config::kDistributorBase + registers.deactive_offset) = registers.bit;
    __asm__ volatile("dsb sy" ::: "memory");
    return wait_for_rwp();
  }

  static auto mask_spi(std::uint32_t intid) noexcept -> bool {
    const arch::gicv3::SpiRegisters registers = resolve_spi(intid);
    if (!registers.valid) {
      return false;
    }
    *mmio32(Config::kDistributorBase + registers.disable_offset) = registers.bit;
    wait_for_rwp();
    return true;
  }

  static auto unmask_spi(std::uint32_t intid) noexcept -> bool {
    const arch::gicv3::SpiRegisters registers = resolve_spi(intid);
    if (!registers.valid) {
      return false;
    }
    *mmio32(Config::kDistributorBase + registers.enable_offset) = registers.bit;
    __asm__ volatile("dsb sy" ::: "memory");
    return true;
  }

  static auto clear_pending_spi(std::uint32_t intid) noexcept -> bool {
    const arch::gicv3::SpiRegisters registers = resolve_spi(intid);
    if (!registers.valid) {
      return false;
    }
    *mmio32(Config::kDistributorBase + registers.clear_offset) = registers.bit;
    __asm__ volatile("dsb sy" ::: "memory");
    return true;
  }

  static auto enable_spi(std::uint32_t intid, std::uint32_t core, arch::gicv3::SpiTrigger trigger) noexcept -> bool {
    return configure_spi(intid, core, trigger) && unmask_spi(intid);
  }

  static auto disable_spi(std::uint32_t intid) noexcept -> bool { return mask_spi(intid); }
};

} // namespace nova::drivers
