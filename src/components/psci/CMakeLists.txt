# psci — guest-facing PSCI (SMCCC 0x8400_xxxx, HVC conduit): power
# control through the standard range; SYSTEM_RESET rides core_vcpu's
# warm reset. The dispatch table (psci_model.hpp) is host-tested.
nova_add_component(psci SOURCES src/psci.cpp DEPS trap_handler core_vcpu)
