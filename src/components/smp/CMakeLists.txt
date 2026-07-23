# smp — physical secondary-core bring-up over the firmware PSCI SMC
# conduit; per-core hardware init runs on the target core itself.
nova_add_component(smp SOURCES src/smp.cpp
    DEPS boot_msg core_gic core_mmu core_timer core_vcpu smmu soft_timer vgic)
