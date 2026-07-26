# demo_hvc — HVC ABI handlers for demo guests (PUTS / PUTC / EXIT).
# Extends HvcService exported by trap_handler; PUTS translation and the
# EXIT path uses the VM lifecycle coordinator.
nova_add_component(demo_hvc SOURCES src/demo_hvc.cpp DEPS trap_handler core_vcpu console_mux smp)
