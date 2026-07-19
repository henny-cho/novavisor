// Boot VM: bring the core-1 peer up, then run the shared vuart flow.

#include "console_demo.h"

int main(void) {
  return run_console_demo(/*peer_vm=*/2);
}
