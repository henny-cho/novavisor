# telemetry — publishes the S layer's region on a soft_timer period of
# its own, so a host reads a snapshot the machine took rather than a
# smear it assembled over the top of a running EL2.
nova_add_component(telemetry SOURCES src/telemetry.cpp DEPS soft_timer)
