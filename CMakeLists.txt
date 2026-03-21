cmake_minimum_required(VERSION 3.20)
project(NovaVisor LANGUAGES C CXX ASM)

# ==============================================================================
# Global C++ & Bare-metal Configuration
# ==============================================================================
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Zero-cost abstraction rules: No RTTI, No Exceptions
add_compile_options(
    $<$<COMPILE_LANGUAGE:CXX>:-fno-rtti>
    $<$<COMPILE_LANGUAGE:CXX>:-fno-exceptions>
    $<$<COMPILE_LANGUAGE:CXX>:-Wall>
    $<$<COMPILE_LANGUAGE:CXX>:-Wextra>
    $<$<COMPILE_LANGUAGE:CXX>:-Wpedantic>
    $<$<COMPILE_LANGUAGE:CXX>:-Werror>
)

# Enable exporting of compile_commands.json for clang-tidy/clangd via VSCode
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ==============================================================================
# Clang-Tidy Configuration (Expert Cross-Compile Support)
# ==============================================================================
option(ENABLE_CLANG_TIDY "Enable clang-tidy during build" OFF)

if(ENABLE_CLANG_TIDY)
    find_program(CLANG_TIDY_EXE NAMES clang-tidy)
    if(CLANG_TIDY_EXE)
        set(CMAKE_CXX_CLANG_TIDY ${CLANG_TIDY_EXE})

        # When cross-compiling for AArch64 bare-metal, clang-tidy needs extra help
        if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU" AND CMAKE_SYSTEM_PROCESSOR STREQUAL "aarch64")
            list(APPEND CMAKE_CXX_CLANG_TIDY --extra-arg=--target=aarch64-none-elf)

            # Use implicit includes from the GCC compiler, but exclude GCC's builtin headers
            # to let Clang use its own resource headers for intrinsics/builtins.
            set(IMPLICIT_INCLUDES ${CMAKE_CXX_IMPLICIT_INCLUDE_DIRECTORIES})

            foreach(inc_path ${IMPLICIT_INCLUDES})
                if(inc_path MATCHES ".*lib/gcc.*")
                    continue()
                endif()
                list(APPEND CMAKE_CXX_CLANG_TIDY "--extra-arg=-isystem${inc_path}")
            endforeach()

            # Print for debugging
            message(STATUS "Clang-Tidy enabled with target: aarch64-none-elf")
        endif()
    else()
        message(WARNING "clang-tidy not found, skipping linting")
    endif()
endif()

# ==============================================================================
# Fetch Dependencies
# ==============================================================================
include(FetchContent)

# 1. Intel compile-time-init-build (cib)
message(STATUS "Fetching Intel cib library...")
FetchContent_Declare(
    cib
    GIT_REPOSITORY https://github.com/intel/compile-time-init-build.git
    GIT_TAG main # or specific release hash
)
FetchContent_MakeAvailable(cib)

# 2. Embedded Template Library (ETL)
message(STATUS "Fetching ETL library...")
FetchContent_Declare(
    etl
    GIT_REPOSITORY https://github.com/ETLCPP/etl.git
    GIT_TAG master
)
FetchContent_MakeAvailable(etl)

# ==============================================================================
# Target Definitions
# ==============================================================================

set(SOURCES
    hal/armv8_aarch64/boot.S
    projects/qemu_virt_arm64/main.cpp
)

add_executable(novavisor.elf ${SOURCES})

# Add Linker script and bare-metal linking rules
target_link_options(novavisor.elf PRIVATE
    -T ${CMAKE_CURRENT_SOURCE_DIR}/hal/armv8_aarch64/linker.ld
    -nostartfiles
    -nostdlib
    -Wl,--gc-sections
)

# add_executable(novavisor.elf ${SOURCES})
# target_link_libraries(novavisor.elf PRIVATE cib etl)
#
# # Custom command to generate raw flat binary required by some QEMU boots
# add_custom_command(TARGET novavisor.elf POST_BUILD
#     COMMAND ${CMAKE_OBJCOPY} -O binary novavisor.elf novavisor.bin
#     COMMENT "Generating flat binary novavisor.bin"
# )
