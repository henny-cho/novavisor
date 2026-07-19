// Phase 15 demo: one guest binary, configuration from the DTB.
//
// The boot vCPU receives its DTB IPA in x0, parses /memory and /cpus
// with the minimal reader, and reports what it found — the harness
// checks the report against the config YAML the hypervisor was built
// with (configs/small.yml vs configs/large.yml), proving the whole
// yml2dtb -> embed -> x0 -> guest-parse pipeline end to end. When the
// config grants a second vCPU, it is actually brought up via PSCI to
// show the value is live, not just advertised.

#include "demo_hvc.h"
#include "fdt_el1.h"
#include "guest_psci.h"

#include <stdint.h>

extern char _secondary_start[]; // common/secondary.S
extern char __stack_top[];      // linker script (boot vCPU's stack)

static void put_dec(uint32_t v) {
  char     buf[10];
  unsigned n = 0;
  do {
    buf[n++] = (char)('0' + (v % 10U));
    v /= 10U;
  } while (v != 0U);
  while (n != 0U) {
    hvc_putc(buf[--n]);
  }
}

// Shared between the vCPUs — same window, MMU off, so a plain
// volatile store/load is the whole protocol (demo 09 pattern).
static volatile uint32_t g_cpu1_done;

// Secondary vCPU (from common/secondary.S, own stack, BSS shared).
// Reporting is the whole job; returning powers it off.
void secondary_main(void) {
  hvc_puts_lit("cpu1 up\n");
  g_cpu1_done = 1;
}

int main(unsigned long dtb) {
  struct fdt_guest_info cfg = fdt_parse_guest(dtb);
  if (!cfg.ok) {
    hvc_puts_lit("cfg: bad dtb\n");
    return 1;
  }

  hvc_puts_lit("cfg: memory ");
  put_dec((uint32_t)(cfg.mem_size >> 20));
  hvc_puts_lit(" MiB, cpus ");
  put_dec(cfg.cpus);
  hvc_putc('\n');

  if (cfg.cpus == 2U) {
    const uint64_t sibling_stack = (uint64_t)__stack_top - 0x10000U;
    if (psci_cpu_on(/*mpidr=*/1, (uint64_t)_secondary_start, sibling_stack) != PSCI_SUCCESS) {
      return 1;
    }
    // CPU_ON is queued cross-core: wait for the sibling's own signal
    // first (AFFINITY_INFO alone races the start request), then for
    // its retirement so the exit below is the last event.
    while (!g_cpu1_done) {
    }
    while (psci_affinity_info(1) != PSCI_AFFINITY_OFF) {
    }
  }
  return 0;
}
