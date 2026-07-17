# demo_hvc — HVC ABI handlers for demo guests (PUTS / PUTC / EXIT).
# Extends HvcService exported by trap_handler.
nova_add_component(demo_hvc SOURCES src/demo_hvc.cpp)
