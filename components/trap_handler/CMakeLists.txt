# trap_handler — EL2 sync/unhandled exception routing + context dump.
# Implements the extern "C" entry points called from hal/armv8_aarch64/vec.S.
add_library(trap_handler OBJECT src/trap_handler.cpp)
target_include_directories(trap_handler PUBLIC ${CMAKE_SOURCE_DIR})
target_link_libraries(trap_handler PUBLIC cib nova_panic)
