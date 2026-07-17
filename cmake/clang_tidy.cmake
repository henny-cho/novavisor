# Clang-Tidy support for cross-compiled bare-metal AArch64.
#
# clang-tidy replays GCC compile commands with Clang's driver, so it must be
# retargeted to aarch64-none-elf and fed GCC's implicit include dirs — minus
# GCC's builtin headers, which must come from Clang's own resource dir for
# intrinsic declarations to resolve.
#
# Rather than wiring CMAKE_CXX_CLANG_TIDY into the build (which would force a
# dedicated build tree and a full recompile per lint run), this writes the
# computed arguments to <build>/clang_tidy_extra_args.txt at configure time.
# `scripts/task.sh lint` feeds them to run-clang-tidy against this build's
# compile_commands.json.

if(NOT (CMAKE_CXX_COMPILER_ID STREQUAL "GNU" AND CMAKE_SYSTEM_PROCESSOR STREQUAL "aarch64"))
    return()
endif()

# Single-dash -extra-arg: run-clang-tidy's argparse rejects the
# double-dash spelling.
set(_tidy_args "-extra-arg=--target=aarch64-none-elf")
foreach(inc_path ${CMAKE_CXX_IMPLICIT_INCLUDE_DIRECTORIES})
    if(inc_path MATCHES ".*lib/gcc.*")
        continue()
    endif()
    list(APPEND _tidy_args "-extra-arg=-isystem${inc_path}")
endforeach()

list(JOIN _tidy_args "\n" _tidy_lines)
file(WRITE "${CMAKE_BINARY_DIR}/clang_tidy_extra_args.txt" "${_tidy_lines}\n")
message(STATUS "Clang-Tidy retarget args written to clang_tidy_extra_args.txt")
