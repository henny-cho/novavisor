# idle — header-only CIB component providing the MainLoop WFI idle.
add_library(idle INTERFACE)
target_include_directories(idle INTERFACE ${CMAKE_SOURCE_DIR})
target_link_libraries(idle INTERFACE cib)
