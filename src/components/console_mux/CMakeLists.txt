# console_mux — line-level guest console multiplexing: per-slot line
# buffers, per-VM output tagging and host-input focus. Passive library
# (no services, no component deps); producers call it directly and the
# scheduler injects the VM-liveness predicate at init.
nova_add_component(console_mux SOURCES src/console_mux.cpp)
