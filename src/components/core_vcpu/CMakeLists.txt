# core_vcpu — VCPU state, cooperative scheduler, EL1 entry via ERET.
# Paired with hal/arch/aarch64/vcpu_enter.S which provides the asm
# primitive; this OBJECT library carries the C++ wiring.
nova_add_component(core_vcpu SOURCES src/core_vcpu.cpp DEPS trap_handler core_mmu vgic nova_panic)
