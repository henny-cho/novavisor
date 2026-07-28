# Shared executable pipeline for guest-hosting project profiles.

# Sources every profile shares: the entry point and the guest-table
# builder. A profile supplies only its own nexus.hpp.
set(NOVA_PROJECT_COMMON_DIR ${CMAKE_CURRENT_LIST_DIR}/../src/projects/common)

function(nova_add_guest_project)
    cmake_parse_arguments(ARG "" "MAIN;GUEST_CONFIG" "INCLUDE_DIRS" ${ARGN})
    if(NOT ARG_MAIN OR NOT ARG_GUEST_CONFIG)
        message(FATAL_ERROR "nova_add_guest_project requires MAIN and GUEST_CONFIG")
    endif()

    set(guest_config_file ${CMAKE_BINARY_DIR}/active_config.yml)
    if(NOT EXISTS ${guest_config_file})
        file(COPY_FILE ${CMAKE_SOURCE_DIR}/configs/default.yml ${guest_config_file})
    endif()
    set(guest_payload_file ${CMAKE_BINARY_DIR}/active_payloads.yml)
    if(NOT EXISTS ${guest_payload_file})
        file(COPY_FILE ${CMAKE_SOURCE_DIR}/configs/payloads.yml ${guest_payload_file})
    endif()
    set(guest_dtb_dir ${CMAKE_BINARY_DIR}/guest_dtb)
    # The DT may only promise what the composition serves: guest-facing
    # PSCI routes through smp (VM power + DMA quiesce), so a profile
    # without it gets a DT with no psci node and no psci enable-method.
    if("psci" IN_LIST NOVA_COMPONENTS)
        set(nova_dtb_psci_flag "")
    else()
        set(nova_dtb_psci_flag "--no-psci")
    endif()
    add_custom_command(
        OUTPUT ${guest_dtb_dir}/guest_dtbs.S
               ${guest_dtb_dir}/device_policy.hpp
        COMMAND python3 ${CMAKE_SOURCE_DIR}/tools/yml2dtb/yml2dtb.py
                ${guest_config_file}
                -o ${guest_dtb_dir}
                --board-layout ${NOVA_BOARD_INCLUDE_DIR}/hal/board/active/board_layout.h
                --inventory ${NOVA_BOARD_DIR}/device_inventory.yml
                --payloads ${guest_payload_file}
                ${nova_dtb_psci_flag}
        DEPENDS ${guest_config_file}
                ${guest_payload_file}
                ${CMAKE_SOURCE_DIR}/tools/yml2dtb/yml2dtb.py
                ${CMAKE_SOURCE_DIR}/src/nova/abi/guest_layout.h
                ${CMAKE_SOURCE_DIR}/src/nova/arch/gicv3/regs.h
                ${NOVA_BOARD_INCLUDE_DIR}/hal/board/active/board_layout.h
                ${NOVA_BOARD_DIR}/device_inventory.yml
        COMMENT "Generating guest payload bundle"
    )

    add_executable(novavisor.elf
        ${ARG_MAIN}
        ${ARG_GUEST_CONFIG}
        ${guest_dtb_dir}/guest_dtbs.S
        ${guest_dtb_dir}/device_policy.hpp
    )
    set_target_properties(novavisor.elf PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})
    target_include_directories(novavisor.elf PRIVATE
        ${CMAKE_SOURCE_DIR}/src
        ${ARG_INCLUDE_DIRS}
        ${guest_dtb_dir}
    )
    target_link_libraries(novavisor.elf PRIVATE
        nova_arch
        nova_platform
        ${NOVA_COMPONENTS}
        cib etl
        nova_warnings
        -Wl,--start-group -lgcc -lc -Wl,--end-group
    )
    target_link_options(novavisor.elf PRIVATE
        -T ${NOVA_LINKER_SCRIPT}
        -nostartfiles
        -nostdlib
        -Wl,--gc-sections
    )
    add_dependencies(novavisor.elf nova_arch_linker_script)
    set_target_properties(novavisor.elf PROPERTIES
        LINK_DEPENDS ${NOVA_LINKER_SCRIPT})

    add_custom_command(TARGET novavisor.elf POST_BUILD
        COMMAND ${CMAKE_SOURCE_DIR}/tools/check_fp_free.sh ${CMAKE_OBJDUMP}
                $<TARGET_FILE:novavisor.elf>
        COMMENT "Checking novavisor.elf is FP/SIMD-free"
    )
    set(image_layout_args)
    if(NOVA_PROJECT_REQUIRE_EMBEDDED_PAYLOAD)
        list(APPEND image_layout_args --require-payload)
    endif()
    add_custom_command(TARGET novavisor.elf POST_BUILD
        COMMAND python3 ${CMAKE_SOURCE_DIR}/tools/check_image_layout.py
                --elf $<TARGET_FILE:novavisor.elf>
                --board-layout ${NOVA_BOARD_INCLUDE_DIR}/hal/board/active/board_layout.h
                --readelf ${CMAKE_READELF}
                --nm ${CMAKE_NM}
                ${image_layout_args}
        COMMENT "Checking linked image layout"
    )
    add_custom_command(TARGET novavisor.elf POST_BUILD
        COMMAND ${CMAKE_OBJCOPY} -O binary
                $<TARGET_FILE:novavisor.elf> ${CMAKE_BINARY_DIR}/novavisor.bin
        BYPRODUCTS ${CMAKE_BINARY_DIR}/novavisor.bin
        COMMENT "Generating flat binary novavisor.bin"
    )
endfunction()

# Shared composition tiers. Each takes every source, including the
# nexus, from src/projects/common/<tier>, so a board-specific profile
# that needs nothing beyond the board selection reduces to one call.
#
#   minimal  — single core, one guest, no device models
#   standard — SMP, guest PSCI, lifecycle, vUART; no IOMMU
#
# A profile needing device passthrough on top of standard supplies its
# own nexus and calls nova_add_guest_project directly.
function(nova_add_minimal_guest_project)
    nova_add_guest_project(
        MAIN ${NOVA_PROJECT_COMMON_DIR}/main.cpp
        GUEST_CONFIG ${NOVA_PROJECT_COMMON_DIR}/guest_config.cpp
        INCLUDE_DIRS
            ${NOVA_PROJECT_COMMON_DIR}/minimal
            ${NOVA_PROJECT_COMMON_DIR}/include
    )
endfunction()

function(nova_add_standard_guest_project)
    nova_add_guest_project(
        MAIN ${NOVA_PROJECT_COMMON_DIR}/main.cpp
        GUEST_CONFIG ${NOVA_PROJECT_COMMON_DIR}/guest_config.cpp
        INCLUDE_DIRS
            ${NOVA_PROJECT_COMMON_DIR}/standard
            ${NOVA_PROJECT_COMMON_DIR}/include
    )
endfunction()
