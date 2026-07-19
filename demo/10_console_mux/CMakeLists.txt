# Phase 14 — console mux: two VMs printing through their own vuarts in
# parallel (tagged, line-atomic), host input focus-routed and echoed
# back over the UART SPI interrupt path.
add_demo_guest(mux_vm0 vm0.c vectors.S)
add_demo_guest(mux_vm2 vm2.c vectors.S)
