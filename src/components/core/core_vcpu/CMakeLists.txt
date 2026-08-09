# core_vcpu — VCPU state, cooperative scheduler, EL1 entry via ERET.
# Paired with hal/arch/aarch64/guest/vcpu_enter.S which provides the asm
# primitive; this OBJECT library carries the C++ wiring.
nova_add_component(core_vcpu SOURCES src/sched.cpp src/lifecycle.cpp DEPS trap_handler core_mmu vgic
                                                                             core_gic soft_timer console_mux trace telemetry)
