# trace — header-only CIB component that places the T layer's rings at
# the board's reserved PA. The rings live in nova/trace.hpp, so emitting
# needs no dependency on this.
nova_add_component(trace)
