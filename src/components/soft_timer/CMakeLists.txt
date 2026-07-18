# soft_timer — multiplexes the CNTHP one-shot into fixed deadline slots
# (scheduler slice, parked-CNTV wake-ups, legacy TIMER_SET).
nova_add_component(soft_timer SOURCES src/soft_timer.cpp DEPS core_gic)
