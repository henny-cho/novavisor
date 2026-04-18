# core_vcpu — Phase 5 EL1 entry via ERET.
# Paired with hal/armv8_aarch64/vcpu_enter.S which provides the asm
# primitive; this OBJECT library carries the C++ wiring.
add_library(core_vcpu OBJECT src/core_vcpu.cpp)
target_include_directories(core_vcpu PUBLIC ${CMAKE_SOURCE_DIR})
target_link_libraries(core_vcpu PUBLIC cib)
