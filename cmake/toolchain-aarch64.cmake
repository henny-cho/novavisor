# NovaVisor AArch64 Bare-metal CMake Toolchain File
# This file tells CMake to cross-compile using the ARM none-elf toolchain.

set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

# Look for the compiler in the PATH or the project's .toolchain directory
get_filename_component(TOOLCHAIN_BIN_DIR "${CMAKE_CURRENT_LIST_DIR}/../.toolchain/current/bin" ABSOLUTE)

# REQUIRED: fail at configure with a clear message if the toolchain is
# missing, instead of an obscure error later (run ./bootstrap).
find_program(CMAKE_C_COMPILER aarch64-none-elf-gcc HINTS "${TOOLCHAIN_BIN_DIR}" REQUIRED)
find_program(CMAKE_CXX_COMPILER aarch64-none-elf-g++ HINTS "${TOOLCHAIN_BIN_DIR}" REQUIRED)
find_program(CMAKE_ASM_COMPILER aarch64-none-elf-gcc HINTS "${TOOLCHAIN_BIN_DIR}" REQUIRED)
find_program(CMAKE_OBJCOPY aarch64-none-elf-objcopy HINTS "${TOOLCHAIN_BIN_DIR}" REQUIRED)
find_program(CMAKE_OBJDUMP aarch64-none-elf-objdump HINTS "${TOOLCHAIN_BIN_DIR}" REQUIRED)
find_program(CMAKE_READELF aarch64-none-elf-readelf HINTS "${TOOLCHAIN_BIN_DIR}" REQUIRED)
find_program(CMAKE_NM aarch64-none-elf-nm HINTS "${TOOLCHAIN_BIN_DIR}" REQUIRED)

# Bare-metal codegen flags common to C/C++/ASM.
# Optimization level is omitted so CMAKE_BUILD_TYPE decides it. -g is
# not here either: these seed the cache once, so an already-configured
# tree would never see it — the root CMakeLists carries it.
# -ffreestanding is NOT used here because cib transitively brings in fmt
# (via cpp-std-extensions non-freestanding path), whose format.cc pulls
# <locale>/<system_error> — headers that omit __throw_* declarations when
# __STDC_HOSTED__=0. -nostdlib is a link-only flag; it lives in
# target_link_options on the final ELF.
# -f{function,data}-sections gives --gc-sections per-symbol granularity;
# linker.ld keeps mandatory sections via KEEP(.text.boot/.text.vec) and
# matches the split names with *(.text*)/*(.data*) wildcards.
# -mcpu names a build profile's core, never a free string: a toolchain
# default could silently compile a new board with the previous board's
# ISA, and an arbitrary override could emit instructions the running CPU
# lacks. The board manifest picks a profile and the profile owns the
# flag — read here because the toolchain file runs before the project.
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES NOVA_BOARD NOVA_CPU_BUILD_PROFILE)
include("${CMAKE_CURRENT_LIST_DIR}/nova_cpu_profiles.cmake")
if(NOT DEFINED NOVA_CPU_BUILD_PROFILE AND DEFINED NOVA_BOARD)
    include("${CMAKE_CURRENT_LIST_DIR}/../src/hal/board/${NOVA_BOARD}/board.cmake" OPTIONAL)
    set(NOVA_CPU_BUILD_PROFILE "${NOVA_BOARD_BUILD_CPU_PROFILE}")
endif()
if(NOT NOVA_CPU_BUILD_PROFILE)
    message(FATAL_ERROR "No build CPU profile for board '${NOVA_BOARD}'")
endif()
nova_cpu_profile_field(build "${NOVA_CPU_BUILD_PROFILE}" mcpu NOVA_CPU_MCPU)

set(COMMON_FLAGS "-mcpu=${NOVA_CPU_MCPU} -mstrict-align -ffunction-sections -fdata-sections")

set(CMAKE_C_FLAGS_INIT   "${COMMON_FLAGS}")
set(CMAKE_CXX_FLAGS_INIT "${COMMON_FLAGS}")
set(CMAKE_ASM_FLAGS_INIT "${COMMON_FLAGS}")

# Bare-metal try_compile needs STATIC_LIBRARY since we have no runtime to link.
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

# Tell CMake not to look for host environment dependencies (like /usr/lib)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
