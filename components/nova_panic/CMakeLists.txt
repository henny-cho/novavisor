# nova_panic — stdx::panic backend converging on UART write + WFI halt.
# Exposes headers via include/ and one TU that specializes stdx::panic_handler.
add_library(nova_panic OBJECT src/nova_panic.cpp)
target_include_directories(nova_panic PUBLIC ${CMAKE_SOURCE_DIR})
target_link_libraries(nova_panic PUBLIC cib)
