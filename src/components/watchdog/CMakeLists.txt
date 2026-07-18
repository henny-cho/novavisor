# watchdog — per-VM heartbeat deadline on a soft_timer slot; a missed
# window warm-resets the hung guest (core_vcpu::reset_vm).
nova_add_component(watchdog SOURCES src/watchdog.cpp DEPS trap_handler core_vcpu soft_timer)
