# SMMUv3 translation model and QEMU runtime.
nova_add_component(smmu SOURCES src/smmu.cpp DEPS core_gic nova_panic)
