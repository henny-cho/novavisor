# hal_init — header-only CIB component for BSS clear during EarlyRuntimeInit.
add_library(hal_init INTERFACE)
target_include_directories(hal_init INTERFACE ${CMAKE_SOURCE_DIR})
target_link_libraries(hal_init INTERFACE cib)
