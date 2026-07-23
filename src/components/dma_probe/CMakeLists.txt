nova_add_component(dma_probe
    SOURCES src/dma_probe.cpp
    DEPS boot_msg core_vcpu dma_device smmu nova_panic)
