# demo_hvc — HVC ABI handlers for demo guests (PUTS / PUTC / EXIT).
# Extends HvcService exported by trap_handler.
add_library(demo_hvc OBJECT src/demo_hvc.cpp)
target_include_directories(demo_hvc PUBLIC ${CMAKE_SOURCE_DIR})
target_link_libraries(demo_hvc PUBLIC cib)
