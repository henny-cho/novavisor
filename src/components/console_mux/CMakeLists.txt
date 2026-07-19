# console_mux — line-level guest console multiplexing: per-slot line
# buffers, per-VM output tagging and host-input focus. Passive library
# (no services); producers call it directly.
nova_add_component(console_mux SOURCES src/console_mux.cpp DEPS core_vcpu)
