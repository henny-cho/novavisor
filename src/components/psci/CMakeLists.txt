# psci — guest-facing PSCI (SMCCC 0x8400_xxxx, HVC conduit): power
# control through the standard range; SYSTEM_OFF/RESET fan out VM-wide
# and CPU_ON routes through the smp cross-call. The dispatch table
# (psci_model.hpp) is host-tested.
nova_add_component(psci SOURCES src/psci.cpp DEPS trap_handler core_vcpu smp)
