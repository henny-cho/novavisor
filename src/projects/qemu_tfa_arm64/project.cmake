# Same board as qemu_virt; the point of this profile is the firmware
# chain underneath, not a different feature set. It therefore composes
# the standard tier — SMP, guest PSCI, lifecycle, vUART — and not device
# passthrough: under a real TF-A the distributor presents two security
# states, so the SPIs an IOMMU needs stay firmware-owned and Non-secure
# EL2 cannot group them. Requesting them anyway stopped this profile's
# boot at SMMU init — a correct refusal, but it left the firmware-handoff
# smoke with nothing to observe. Device isolation beneath a secure-world
# firmware is its own contract, on the board that needs it.
include(${CMAKE_CURRENT_LIST_DIR}/../qemu_virt_arm64_standard/project.cmake)
set(NOVA_PROJECT_BOARD "qemu_tfa")
# BL33 travels inside the FIP; there is no -device loader in -bios mode.
set(NOVA_PROJECT_REQUIRE_EMBEDDED_PAYLOAD TRUE)
