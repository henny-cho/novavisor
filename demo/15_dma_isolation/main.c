#include "demo_hvc.h"
#include "guest_psci.h"
#include "nova/abi/guest_layout.h"

#include <stdint.h>

#define RUN_COUNT (*(volatile uint64_t*)(NOVA_IVC_SHM_IPA + 0xFC0))

int main(void) {
  const uint64_t run = ++RUN_COUNT;
  if (run == 1) {
    hvc_puts_lit("dma lifecycle boot 1\n");
    psci_system_reset();
  }

  if (run == 2) {
    hvc_puts_lit("dma lifecycle boot 2\n");
    if (hvc_dma_fault_inject() != 0) {
      hvc_puts_lit("dma runtime fault request failed\n");
      return 1;
    }
    for (;;) {
      hvc_yield();
    }
  }

  hvc_puts_lit("dma lifecycle boot 3\n");
  return 0;
}
