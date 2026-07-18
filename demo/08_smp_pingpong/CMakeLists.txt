# Phase 12 — SMP: two VMs on two physical cores, SPSC-ring pingpong.
# smp_ prefix: 03_ivc_pingpong already owns the bare ping/pong targets.
add_demo_guest(smp_ping main_ping.c)
add_demo_guest(smp_pong main_pong.c)
