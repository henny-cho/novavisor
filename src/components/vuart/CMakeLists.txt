# vuart — emulated PL011 per VM: MMIO frame emulation, TX into the
# console mux, host RX injection with SPI delivery through the vGIC
# routing (IROUTER) and the smp cross-call when the target vCPU lives
# on another core.
nova_add_component(vuart SOURCES src/vuart.cpp DEPS trap_handler core_gic core_vcpu smp vgic console_mux)
