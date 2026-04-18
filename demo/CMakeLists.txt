# NovaVisor demo guests — EL1 bare-metal programs loaded by the hypervisor.
#
# This is an independent CMake project (not part of the hypervisor build).
# It uses the same aarch64-none-elf cross toolchain but targets EL1
# (no hypervisor CIB/ETL dependencies, no -fno-rtti/-fno-exceptions since
# all demos are C).
#
# Invoked by scripts/task.sh demo build / verify / verify-all.

cmake_minimum_required(VERSION 3.25)
project(novavisor_demos LANGUAGES C ASM)

# Verify we're using the cross toolchain. scripts/task.sh sources
# .toolchain/env.sh before invoking cmake, which puts aarch64-none-elf-gcc
# on PATH and sets CC via the toolchain file.
if(NOT CMAKE_C_COMPILER MATCHES "aarch64-none-elf")
    message(FATAL_ERROR
        "Demo guests require the aarch64-none-elf cross toolchain. "
        "Run 'source .toolchain/env.sh' or invoke via scripts/task.sh.")
endif()

set(CMAKE_C_STANDARD 17)
set(CMAKE_C_STANDARD_REQUIRED ON)

# EL1 bare-metal: no libc startup, no stack protector (no runtime).
# -fno-builtin prevents gcc from lowering memcpy/memset into libc calls
# we don't link.
add_compile_options(
    -ffreestanding
    -nostdlib
    -fno-builtin
    -fno-stack-protector
    -Wall -Wextra
    -O2
    -g
)
add_link_options(-nostdlib -static -Wl,--build-id=none)

set(DEMO_COMMON_DIR ${CMAKE_CURRENT_SOURCE_DIR}/common)

# Shared startup + linker fragments. Every demo links against these.
add_library(demo_common OBJECT
    ${DEMO_COMMON_DIR}/startup.S
)
target_include_directories(demo_common PUBLIC ${DEMO_COMMON_DIR}/include)

# add_demo_guest(<name> <sources...>)
#   Produces <name> (ELF) and <name>.bin (raw binary for QEMU -device loader).
#   The binary is what the hypervisor actually consumes at the manifest's
#   load_addr.
function(add_demo_guest name)
    add_executable(${name} ${ARGN} $<TARGET_OBJECTS:demo_common>)
    target_include_directories(${name} PRIVATE ${DEMO_COMMON_DIR}/include)
    target_link_options(${name} PRIVATE
        -T ${DEMO_COMMON_DIR}/linker.ld
    )
    # Produce raw binary next to the ELF. The demo runner reads
    # build/demo/<manifest-dir>/<binary> where <binary> is manifest.guests[i].binary.
    # Use an explicit output path — mixing a generator expression with a
    # ".bin" suffix in BYPRODUCTS trips the Ninja generator.
    set(_bin_path ${CMAKE_CURRENT_BINARY_DIR}/${name}.bin)
    add_custom_command(TARGET ${name} POST_BUILD
        COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:${name}> ${_bin_path}
        BYPRODUCTS ${_bin_path}
        COMMENT "objcopy ${name} -> ${name}.bin"
    )
endfunction()

# Enable demos phase-by-phase. A demo's add_subdirectory line is added
# when its phase starts; the demo itself is marked enabled=true in
# manifest.yml only once the hypervisor features pass its manifest checks.
add_subdirectory(01_hello)
# add_subdirectory(02_timer)          # Phase 6
# add_subdirectory(03_ivc_pingpong)   # Phase 7
# 04_zephyr+ reference external images; no cmake subdir needed.
