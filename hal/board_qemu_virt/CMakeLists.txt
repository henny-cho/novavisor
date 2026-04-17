# board_qemu_virt — header-only MMIO descriptors + PL011 UART for QEMU virt.
add_library(hal_board_qemu_virt INTERFACE)
target_include_directories(hal_board_qemu_virt INTERFACE ${CMAKE_SOURCE_DIR})
