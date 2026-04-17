# NovaVisor AArch64 Bare-metal CMake Toolchain File
# This file tells CMake to cross-compile using the ARM none-elf toolchain.

set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

# Look for the compiler in the PATH or the project's .toolchain directory
get_filename_component(TOOLCHAIN_BIN_DIR "${CMAKE_CURRENT_LIST_DIR}/../.toolchain/current/bin" ABSOLUTE)

find_program(CMAKE_C_COMPILER aarch64-none-elf-gcc HINTS "${TOOLCHAIN_BIN_DIR}")
find_program(CMAKE_CXX_COMPILER aarch64-none-elf-g++ HINTS "${TOOLCHAIN_BIN_DIR}")
find_program(CMAKE_ASM_COMPILER aarch64-none-elf-gcc HINTS "${TOOLCHAIN_BIN_DIR}")
find_program(CMAKE_OBJCOPY aarch64-none-elf-objcopy HINTS "${TOOLCHAIN_BIN_DIR}")
find_program(CMAKE_OBJDUMP aarch64-none-elf-objdump HINTS "${TOOLCHAIN_BIN_DIR}")

set(CMAKE_C_COMPILER_WORKS 1)
set(CMAKE_CXX_COMPILER_WORKS 1)

# Bare-metal codegen flags common to C/C++/ASM.
# Optimization level and -g are deliberately omitted: CMake's
# CMAKE_{C,CXX}_FLAGS_{DEBUG,RELEASE} apply those so CMAKE_BUILD_TYPE is
# honored (Debug = -O0 -g, Release = -O3 -DNDEBUG).
# -ffreestanding is NOT used here because cib transitively brings in fmt
# (via cpp-std-extensions non-freestanding path), whose format.cc pulls
# <locale>/<system_error> — headers that omit __throw_* declarations when
# __STDC_HOSTED__=0. -nostdlib is a link-only flag; it lives in
# target_link_options on the final ELF.
set(COMMON_FLAGS "-mcpu=cortex-a57 -mstrict-align")

set(CMAKE_C_FLAGS_INIT   "${COMMON_FLAGS}")
set(CMAKE_CXX_FLAGS_INIT "${COMMON_FLAGS}")
set(CMAKE_ASM_FLAGS_INIT "${COMMON_FLAGS}")

# Bare-metal try_compile needs STATIC_LIBRARY since we have no runtime to link.
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

# Tell CMake not to look for host environment dependencies (like /usr/lib)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
