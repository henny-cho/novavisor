# NovaVisor AArch64 Bare-metal CMake Toolchain File
# This file tells CMake to cross-compile using the ARM none-elf toolchain.

set(CMAKE_SYSTEM_NAME Generic)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

# Look for the compiler in the PATH
# setup_env.sh or devcontainer adds aarch64-none-elf to PATH
find_program(CMAKE_C_COMPILER aarch64-none-elf-gcc)
find_program(CMAKE_CXX_COMPILER aarch64-none-elf-g++)
find_program(CMAKE_ASM_COMPILER aarch64-none-elf-gcc)
find_program(CMAKE_OBJCOPY aarch64-none-elf-objcopy)
find_program(CMAKE_OBJDUMP aarch64-none-elf-objdump)

set(CMAKE_C_COMPILER_WORKS 1)
set(CMAKE_CXX_COMPILER_WORKS 1)

# Basic bare-metal flags
set(COMMON_FLAGS "-mcpu=cortex-a57 -static -nostdlib -fno-builtin -mstrict-align")

# Arch specific tuning
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} ${COMMON_FLAGS} -O2 -g")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} ${COMMON_FLAGS} -O2 -g")
set(CMAKE_ASM_FLAGS "${CMAKE_ASM_FLAGS} ${COMMON_FLAGS}")

# Tell CMake not to look for host environment dependencies (like /usr/lib)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
