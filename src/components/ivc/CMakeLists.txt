# ivc — inter-VM doorbell: HVC_IVC_DOORBELL → vIRQ on the target VCPU.
# The shared-memory mapping lives in core_mmu; the protocol in the page
# is guest-owned.
nova_add_component(ivc SOURCES src/ivc.cpp DEPS trap_handler core_vcpu)
