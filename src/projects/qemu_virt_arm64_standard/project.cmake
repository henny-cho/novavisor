set(NOVA_PROJECT_ARCH "aarch64")
set(NOVA_PROJECT_BOARD "qemu_virt")
set(NOVA_PROJECT_CAPABILITIES
    gicv3
)

# The composition a board needs for SMP, guest PSCI, lifecycle recovery
# and a console — the tier real-board bring-up closes before an IOMMU is
# characterized. Device isolation (smmu, dma_device, dma_probe) is
# deliberately absent: VM power reaches it through DmaQuiesceService, so
# its absence means "no DMA to isolate", not "no VM power".
set(NOVA_COMPONENTS
    nova_panic
    trace
    trap_handler
    boot_msg
    dtb_parser
    core_mmu
    core_gic
    vgic
    soft_timer
    core_timer
    core_vcpu
    console_mux
    demo_hvc
    ivc
    psci
    watchdog
    smp
    vuart
)
