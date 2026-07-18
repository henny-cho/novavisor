# trap_handler — EL2 sync/unhandled exception routing + context dump.
# Implements the extern "C" entry points called from hal/arch/aarch64/vec.S.
nova_add_component(trap_handler SOURCES src/trap_handler.cpp DEPS nova_panic)
