# command — drains the host's command ring on a dedicated soft_timer
# period, so how long a command waits is a number the firmware declares
# rather than a side effect of how busy the machine is.
nova_add_component(command SOURCES src/command.cpp DEPS soft_timer trace)
