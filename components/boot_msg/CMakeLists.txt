# boot_msg — header-only CIB component for UART boot banner on RuntimeStart.
add_library(boot_msg INTERFACE)
target_include_directories(boot_msg INTERFACE ${CMAKE_SOURCE_DIR})
target_link_libraries(boot_msg INTERFACE cib)
