// components/trap_handler/src/trap_dump.cpp
//
// TrapContext register dump — shared by every fatal trap path.

#include "hal/console.hpp"
#include "nova/arch/trap_context.hpp"
#include "trap_handler/trap_handler.hpp"

#include <cstddef>

namespace nova {

void dump_trap_context(const TrapContext* ctx) noexcept {
  using console::write;
  using console::write_dec64;
  using console::write_hex64;

  write("--- EL2 TRAP DUMP ---\n");

  write("ESR_EL2 : 0x");
  write_hex64(ctx->esr);
  write("  FAR_EL2 : 0x");
  write_hex64(ctx->far);
  write("\n");

  write("ELR_EL2 : 0x");
  write_hex64(ctx->elr);
  write("  SPSR    : 0x");
  write_hex64(ctx->spsr);
  write("\n");

  write("SP_EL1  : 0x");
  write_hex64(ctx->sp);
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
