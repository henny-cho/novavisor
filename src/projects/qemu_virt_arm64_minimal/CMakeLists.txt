set(full_project_dir ${CMAKE_SOURCE_DIR}/src/projects/qemu_virt_arm64)

nova_add_guest_project(
    MAIN ${CMAKE_CURRENT_SOURCE_DIR}/main.cpp
    GUEST_CONFIG ${full_project_dir}/guest_config.cpp
    INCLUDE_DIRS
        ${CMAKE_CURRENT_SOURCE_DIR}
        ${full_project_dir}/include
)
