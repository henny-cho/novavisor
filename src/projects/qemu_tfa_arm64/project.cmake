# Same composition as the full QEMU virt project — the point of this
# profile is the firmware chain underneath, not a different feature set.
include(${CMAKE_CURRENT_LIST_DIR}/../qemu_virt_arm64/project.cmake)
set(NOVA_PROJECT_BOARD "qemu_tfa")
# BL33 travels inside the FIP; there is no -device loader in -bios mode.
set(NOVA_PROJECT_REQUIRE_EMBEDDED_PAYLOAD TRUE)
