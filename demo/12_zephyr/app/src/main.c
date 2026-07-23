#include <stdint.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

int main(void) {
  uint32_t sequence = 1;
  for (;;) {
    printk("NOVA ZEPHYR HEARTBEAT %u\n", sequence++);
    k_sleep(K_SECONDS(5));
  }
  return 0;
}
