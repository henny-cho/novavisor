# watchdog — per-VM heartbeat deadline on a soft_timer slot; a missed
# window warm-resets the hung guest. Arm/cancel work is affinity-routed
# to the VM boot vCPU's core, which owns the single deadline slot.
nova_add_component(watchdog SOURCES src/watchdog.cpp DEPS trap_handler core_vcpu smp soft_timer)
