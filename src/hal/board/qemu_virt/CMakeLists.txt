# Header-only MMIO descriptors and active facade for the selected board.
add_library(nova_board INTERFACE)
target_include_directories(nova_board INTERFACE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_SOURCE_DIR}/src)
