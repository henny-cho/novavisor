# smp — physical secondary-core bring-up over the firmware PSCI SMC
# conduit; per-core hardware init runs on the target core itself.
nova_add_component(smp SOURCES src/smp.cpp src/cross_call.cpp src/vm_lifecycle.cpp
    DEPS boot_msg core_gic core_mmu core_timer core_vcpu soft_timer trap_handler vgic)
