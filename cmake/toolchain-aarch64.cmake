# NovaVisor AArch64 Bare-metal CMake Toolchain File
# This file tells CMake to cross-compile using the ARM none-elf toolchain.

set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

# Look for the compiler in the PATH or the project's .toolchain directory
get_filename_component(TOOLCHAIN_BIN_DIR "${CMAKE_CURRENT_LIST_DIR}/../.toolchain/current/bin" ABSOLUTE)

# REQUIRED: fail at configure with a clear message if the toolchain is
# missing, instead of an obscure error later (run scripts/setup_env.sh).
find_program(CMAKE_C_COMPILER aarch64-none-elf-gcc HINTS "${TOOLCHAIN_BIN_DIR}" REQUIRED)
find_program(CMAKE_CXX_COMPILER aarch64-none-elf-g++ HINTS "${TOOLCHAIN_BIN_DIR}" REQUIRED)
find_program(CMAKE_ASM_COMPILER aarch64-none-elf-gcc HINTS "${TOOLCHAIN_BIN_DIR}" REQUIRED)
find_program(CMAKE_OBJCOPY aarch64-none-elf-objcopy HINTS "${TOOLCHAIN_BIN_DIR}" REQUIRED)
find_program(CMAKE_OBJDUMP aarch64-none-elf-objdump HINTS "${TOOLCHAIN_BIN_DIR}" REQUIRED)
find_program(CMAKE_READELF aarch64-none-elf-readelf HINTS "${TOOLCHAIN_BIN_DIR}" REQUIRED)
find_program(CMAKE_NM aarch64-none-elf-nm HINTS "${TOOLCHAIN_BIN_DIR}" REQUIRED)

# Bare-metal codegen flags common to C/C++/ASM.
# Optimization level and -g are deliberately omitted: CMake's
# CMAKE_{C,CXX}_FLAGS_{DEBUG,RELEASE} apply those so CMAKE_BUILD_TYPE is
# honored (Debug = -O0 -g, Release = -O3 -DNDEBUG).
# -ffreestanding is NOT used here because cib transitively brings in fmt
# (via cpp-std-extensions non-freestanding path), whose format.cc pulls
# <locale>/<system_error> — headers that omit __throw_* declarations when
# __STDC_HOSTED__=0. -nostdlib is a link-only flag; it lives in
# target_link_options on the final ELF.
# -f{function,data}-sections gives --gc-sections per-symbol granularity;
# linker.ld keeps mandatory sections via KEEP(.text.boot/.text.vec) and
# matches the split names with *(.text*)/*(.data*) wildcards.
# -mcpu must name the selected board's core: a toolchain default could
# silently compile a new board with the previous board's ISA. The board
# manifest is the single source — read it here (the toolchain file runs
# before the project, so NOVA_BOARD_REQUIRED_CPU is not yet in scope) and
# let an explicit -DNOVA_BOARD_CPU override for experiments.
list(APPEND CMAKE_TRY_COMPILE_PLATFORM_VARIABLES NOVA_BOARD NOVA_BOARD_CPU)
if(NOT DEFINED NOVA_BOARD_CPU AND DEFINED NOVA_BOARD)
    include("${CMAKE_CURRENT_LIST_DIR}/../src/hal/board/${NOVA_BOARD}/board.cmake" OPTIONAL)
    set(NOVA_BOARD_CPU "${NOVA_BOARD_REQUIRED_CPU}")
endif()
if(NOT NOVA_BOARD_CPU MATCHES "^[A-Za-z0-9_.+-]+$")
    message(FATAL_ERROR "NOVA_BOARD_CPU could not be resolved for board '${NOVA_BOARD}'")
endif()

set(COMMON_FLAGS "-mcpu=${NOVA_BOARD_CPU} -mstrict-align -ffunction-sections -fdata-sections")

set(CMAKE_C_FLAGS_INIT   "${COMMON_FLAGS}")
set(CMAKE_CXX_FLAGS_INIT "${COMMON_FLAGS}")
set(CMAKE_ASM_FLAGS_INIT "${COMMON_FLAGS}")

# Bare-metal try_compile needs STATIC_LIBRARY since we have no runtime to link.
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

# Tell CMake not to look for host environment dependencies (like /usr/lib)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
