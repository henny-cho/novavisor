# core_mmu — Stage 2 MMU component.
# Headers are shared with host GTest (stage2_descriptor/stage2_builder);
# the .cpp/.S pair is cross-compile only (references EL2 system registers).
add_library(core_mmu OBJECT src/core_mmu.cpp)
target_include_directories(core_mmu PUBLIC ${CMAKE_SOURCE_DIR})
target_link_libraries(core_mmu PUBLIC cib)
