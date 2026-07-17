# core_vcpu — Phase 5 EL1 entry via ERET.
# Paired with hal/armv8_aarch64/vcpu_enter.S which provides the asm
# primitive; this OBJECT library carries the C++ wiring.
nova_add_component(core_vcpu SOURCES src/core_vcpu.cpp)
