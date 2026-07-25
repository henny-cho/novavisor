# TF-A-chained QEMU virt profile: identical composition to
# qemu_virt_arm64, so its nexus.hpp is reused via the include path.
nova_add_guest_project(
    MAIN ${NOVA_PROJECT_COMMON_DIR}/main.cpp
    GUEST_CONFIG ${NOVA_PROJECT_COMMON_DIR}/guest_config.cpp
    INCLUDE_DIRS
        ${CMAKE_CURRENT_SOURCE_DIR}/../qemu_virt_arm64
        ${NOVA_PROJECT_COMMON_DIR}/include
)
