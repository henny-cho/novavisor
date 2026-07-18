# nova_panic — stdx::panic backend converging on UART write + WFI halt.
# Exposes headers via include/ and one TU that specializes stdx::panic_handler.
nova_add_component(nova_panic SOURCES src/nova_panic.cpp)
