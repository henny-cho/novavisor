# Full QEMU virt profile: the shared entry point plus this directory's
# own nexus.hpp (17 components, SMP and device passthrough included).
nova_add_guest_project(
    MAIN ${NOVA_PROJECT_COMMON_DIR}/main.cpp
    GUEST_CONFIG ${NOVA_PROJECT_COMMON_DIR}/guest_config.cpp
    INCLUDE_DIRS
        ${CMAKE_CURRENT_SOURCE_DIR}
        ${NOVA_PROJECT_COMMON_DIR}/include
)
