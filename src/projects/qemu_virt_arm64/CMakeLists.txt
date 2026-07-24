nova_add_guest_project(
    MAIN ${CMAKE_CURRENT_SOURCE_DIR}/main.cpp
    GUEST_CONFIG ${CMAKE_CURRENT_SOURCE_DIR}/guest_config.cpp
    INCLUDE_DIRS ${CMAKE_CURRENT_SOURCE_DIR}/include
)
