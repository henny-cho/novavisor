# One binary, two vCPUs: the image carries both the boot path
# (common startup) and the CPU_ON entry (common secondary stub).
add_demo_guest(guest_smp main.c vectors.S ../common/secondary.S)
