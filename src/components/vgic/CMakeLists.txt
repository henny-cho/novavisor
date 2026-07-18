nova_add_component(vgic
    SOURCES src/vgic.cpp
    DEPS trap_handler core_gic
)
