# Shared executable pipeline for guest-hosting project profiles.

function(nova_add_guest_project)
    cmake_parse_arguments(ARG "" "MAIN;GUEST_CONFIG" "INCLUDE_DIRS" ${ARGN})
    if(NOT ARG_MAIN OR NOT ARG_GUEST_CONFIG)
        message(FATAL_ERROR "nova_add_guest_project requires MAIN and GUEST_CONFIG")
    endif()

    set(guest_config_file ${CMAKE_BINARY_DIR}/active_config.yml)
    if(NOT EXISTS ${guest_config_file})
        file(COPY_FILE ${CMAKE_SOURCE_DIR}/configs/default.yml ${guest_config_file})
    endif()
    set(guest_dtb_dir ${CMAKE_BINARY_DIR}/guest_dtb)
    add_custom_command(
        OUTPUT ${guest_dtb_dir}/guest_dtbs.S
               ${guest_dtb_dir}/device_policy.hpp
        COMMAND python3 ${CMAKE_SOURCE_DIR}/tools/yml2dtb/yml2dtb.py
                ${guest_config_file}
                -o ${guest_dtb_dir}
                --board-layout ${NOVA_BOARD_INCLUDE_DIR}/board_layout.h
                --inventory ${NOVA_BOARD_DIR}/device_inventory.yml
        DEPENDS ${guest_config_file}
                ${CMAKE_SOURCE_DIR}/tools/yml2dtb/yml2dtb.py
                ${CMAKE_SOURCE_DIR}/src/nova/abi/guest_layout.h
                ${CMAKE_SOURCE_DIR}/src/nova/arch/gicv3_regs.h
                ${NOVA_BOARD_INCLUDE_DIR}/board_layout.h
                ${NOVA_BOARD_DIR}/device_inventory.yml
        COMMENT "Generating guest configuration"
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
        COMMAND ${CMAKE_SOURCE_DIR}/scripts/check_fp_free.sh ${CMAKE_OBJDUMP}
                $<TARGET_FILE:novavisor.elf>
        COMMENT "Checking novavisor.elf is FP/SIMD-free"
    )
    add_custom_command(TARGET novavisor.elf POST_BUILD
        COMMAND ${CMAKE_OBJCOPY} -O binary
                $<TARGET_FILE:novavisor.elf> ${CMAKE_BINARY_DIR}/novavisor.bin
        BYPRODUCTS ${CMAKE_BINARY_DIR}/novavisor.bin
        COMMENT "Generating flat binary novavisor.bin"
    )
endfunction()
