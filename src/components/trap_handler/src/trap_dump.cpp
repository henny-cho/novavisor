// components/trap_handler/src/trap_dump.cpp
//
// TrapContext register dump — shared by every fatal trap path. Wide
// enough to attribute a first failure from a serial log alone: which
// core, which translation/vector configuration it was running with,
// the stage-2 fault IPA, and an image-relative ELR that stays
// symbolizable across board-specific link bases.

#include "hal/console.hpp"
#include "hal/cpu.hpp"
#include "hal/diag.hpp"
#include "nova/arch/esr.hpp"
#include "nova/arch/trap_context.hpp"
#include "trap_handler/trap_handler.hpp"

#include <cstddef>
#include <string_view>

namespace nova {

namespace {

[[nodiscard]] auto ec_name(esr::ExceptionClass ec) noexcept -> std::string_view {
  using esr::ExceptionClass;
  switch (ec) {
  case ExceptionClass::kWfx:
    return "WFx";
  case ExceptionClass::kFpSimd:
    return "FP/SIMD";
  case ExceptionClass::kHvcAa64:
    return "HVC";
  case ExceptionClass::kSmcAa64:
    return "SMC";
  case ExceptionClass::kMsrMrs:
    return "sysreg";
  case ExceptionClass::kInstAbortLower:
    return "inst abort (lower)";
  case ExceptionClass::kInstAbortCurrent:
    return "inst abort (EL2)";
  case ExceptionClass::kDataAbortLower:
    return "data abort (lower)";
  case ExceptionClass::kDataAbortCurrent:
    return "data abort (EL2)";
  case ExceptionClass::kSpAlign:
    return "SP alignment";
  case ExceptionClass::kSerror:
    return "SError";
  default:
    return "?";
  }
}

} // namespace

void dump_trap_context(const TrapContext* ctx) noexcept {
  using console::write;
  using console::write_dec64;
  using console::write_hex64;

  const diag::El2State state = diag::snapshot();
  const auto           ec    = esr::get_ec(ctx->esr);

  write("--- EL2 TRAP DUMP ---\n");

  write("CPU     : ");
  write_dec64(cpu::id());
  write("  EC : 0x");
  write_hex64(static_cast<std::uint64_t>(ec));
  write(" (");
  write(ec_name(ec));
  write(")\n");

  write("ESR_EL2 : 0x");
  write_hex64(ctx->esr);
  write("  FAR_EL2 : 0x");
  write_hex64(ctx->far);
  write("\n");

  write("HPFAR   : 0x");
  write_hex64(state.hpfar);
  write("  (IPA 0x");
  write_hex64((state.hpfar >> 4U) << 12U); // HPFAR_EL2.FIPA[43:4] → IPA[51:12]
  write(")\n");

  write("ELR_EL2 : 0x");
  write_hex64(ctx->elr);
  write("  (text+0x");
  write_hex64(ctx->elr - state.text_base);
  write(")\n");

  write("SPSR    : 0x");
  write_hex64(ctx->spsr);
  write("\n");

  // The frame sits at the fault-time SP (vec.S allocates it there), so
  // SP_EL2 is recoverable without having been saved.
  write("SP_EL1  : 0x");
  write_hex64(ctx->sp);
  write("  SP_EL2  : 0x");
  write_hex64(reinterpret_cast<std::uint64_t>(ctx) + sizeof(TrapContext));
  write("\n");

  write("VBAR    : 0x");
  write_hex64(state.vbar);
  write("  SCTLR : 0x");
  write_hex64(state.sctlr);
  write("  HCR : 0x");
  write_hex64(state.hcr);
  write("\n");

  for (std::size_t i = 0; i < ctx->x.size(); ++i) {
    write("x");
    write_dec64(i);
    write(" : 0x");
    write_hex64(ctx->x[i]);
    write("\n");
  }

  write("---------------------\n");
}

} // namespace nova
