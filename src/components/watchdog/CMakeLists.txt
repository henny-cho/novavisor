# watchdog — per-VM heartbeat deadline on a soft_timer slot; a missed
# window warm-resets the hung guest (smp::reset_vm — the VM's vcpu 0
# may live on another core).
nova_add_component(watchdog SOURCES src/watchdog.cpp DEPS trap_handler core_vcpu smp soft_timer)
