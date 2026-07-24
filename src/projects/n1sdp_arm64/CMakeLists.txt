set(common_project_dir ${CMAKE_SOURCE_DIR}/src/projects/common)

nova_add_guest_project(
    MAIN ${common_project_dir}/minimal/main.cpp
    GUEST_CONFIG ${common_project_dir}/guest_config.cpp
    INCLUDE_DIRS
        ${common_project_dir}/minimal
        ${common_project_dir}/include
)
