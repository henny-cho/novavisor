# core_mmu — Stage 2 MMU component.
# Headers are shared with host GTest (stage2_descriptor/stage2_builder);
# the .cpp/.S pair is cross-compile only (references EL2 system registers).
nova_add_component(core_mmu SOURCES src/core_mmu.cpp)
