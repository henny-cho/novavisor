# core_timer — hypervisor timer service: HVC_TIMER_SET arms CNTHP,
# expiry injects the virtual timer PPI into the guest.
nova_add_component(core_timer SOURCES src/core_timer.cpp DEPS core_gic trap_handler core_vcpu)
