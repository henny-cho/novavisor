# vuart — emulated PL011 per VM: MMIO frame emulation, TX into the
# console mux, host RX injection with SPI delivery through the vGIC
# routing (IROUTER); the vGIC reevaluate fan-out reaches remote cores.
nova_add_component(vuart SOURCES src/vuart.cpp DEPS trap_handler core_gic core_vcpu vgic console_mux telemetry)
