set(common_project_dir ${CMAKE_SOURCE_DIR}/src/projects/common)

nova_add_guest_project(
    MAIN ${CMAKE_CURRENT_SOURCE_DIR}/main.cpp
    GUEST_CONFIG ${common_project_dir}/guest_config.cpp
    INCLUDE_DIRS
        ${CMAKE_CURRENT_SOURCE_DIR}
        ${common_project_dir}/include
)
