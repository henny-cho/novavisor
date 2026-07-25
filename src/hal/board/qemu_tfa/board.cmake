# QEMU virt behind a real TF-A chain (-bios BL1+FIP instead of -kernel).
# Same machine and capabilities as qemu_virt; the layout moves the image
# to TF-A's BL33 address. Not a hardware board: it runs wherever the
# qemu_virt profile runs, but exercises the firmware handoff contract
# (EL2 entry state, PSCI SMC conduit served by BL31).
set(NOVA_BOARD_ARCH "aarch64")
set(NOVA_BOARD_REQUIRED_CPU "cortex-a57")
set(NOVA_BOARD_CAPABILITIES
    gicv3
    smmuv3
    dma
)
set(NOVA_BOARD_FIRMWARE_CHAIN "trusted-firmware-a:qemu")
