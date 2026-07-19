// Core-1 peer: the same vuart flow, started by vm0's HVC_VM_START.

#include "console_demo.h"

int main(void) {
  return run_console_demo(/*peer_vm=*/-1);
}
