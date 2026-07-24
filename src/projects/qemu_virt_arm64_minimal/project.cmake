set(NOVA_PROJECT_ARCH "aarch64")
set(NOVA_PROJECT_BOARD "qemu_virt")
set(NOVA_PROJECT_CAPABILITIES
    gicv3
)

set(NOVA_COMPONENTS
    nova_panic
    trap_handler
    boot_msg
    dtb_parser
    core_mmu
    core_gic
    vgic
    soft_timer
    core_timer
    console_mux
    core_vcpu
)
