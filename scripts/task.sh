#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# NovaVisor Task Runner
# Command central for development cycle, CI, and repetitive tasks.
# ==============================================================================

WORK_DIR="$(pwd)"
BUILD_DIR="${WORK_DIR}/build"
TOOLCHAIN_FILE="${WORK_DIR}/cmake/toolchain-aarch64.cmake"

# Load the toolchain environment
if [ -f "${WORK_DIR}/.toolchain/env.sh" ]; then
    source "${WORK_DIR}/.toolchain/env.sh"
else
    echo "Warning: .toolchain/env.sh not found. Run scripts/setup_env.sh first."
fi

print_usage() {
    echo "Usage: $0 <subcommand> [options]"
    echo ""
    echo "Subcommands:"
    echo "  build       Configure and build the project using CMake & Ninja"
    echo "  clean       Remove the build directory"
    echo "  format      Run clang-format on all C/C++ source files"
    echo "  lint        Run clang-tidy static analysis"
    echo "  run         Run novavisor.elf in QEMU"
    echo "  ci          Run the full CI pipeline steps locally"
    echo ""
    echo "Options:"
    echo "  --release   Build in Release mode (default is Debug)"
    echo "  -h, --help  Show this help message"
}

# ------------------------------------------------------------------------------
# Subcommand implementations
# ------------------------------------------------------------------------------

cmd_build() {
    local BUILD_TYPE="Debug"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --release) BUILD_TYPE="Release"; shift ;;
            *) echo "Unknown option: $1"; print_usage; exit 1 ;;
        esac
    done

    echo "==> Configuring CMake (${BUILD_TYPE})..."
    cmake -B "${BUILD_DIR}" -G Ninja -DCMAKE_TOOLCHAIN_FILE="${TOOLCHAIN_FILE}" -DCMAKE_BUILD_TYPE="${BUILD_TYPE}"

    echo "==> Building..."
    cmake --build "${BUILD_DIR}"
}

cmd_clean() {
    echo "==> Cleaning build directory..."
    rm -rf "${BUILD_DIR}"
    echo "Clean complete."
}

cmd_format() {
    echo "==> Running clang-format..."
    find . -type d \( -name "external" -o -name ".toolchain" -o -name "build" \) -prune -o -type f \( -name '*.cpp' -o -name '*.hpp' -o -name '*.c' -o -name '*.h' \) -print | xargs -r clang-format -i
    echo "Formatting complete."
}

cmd_lint() {
    echo "==> Running clang-tidy via CMake..."
    # Re-configure with clang-tidy enabled
    cmake -B "${BUILD_DIR}" -DENABLE_CLANG_TIDY=ON -DCMAKE_BUILD_TYPE=Debug
    # Run the build which will invoke clang-tidy
    cmake --build "${BUILD_DIR}"
    echo "Linting complete."
}

cmd_run() {
    echo "==> Running NovaVisor in QEMU..."
    if [ ! -f "${BUILD_DIR}/novavisor.elf" ]; then
        echo "Executable not found. Building first..."
        cmd_build
    fi
    qemu-system-aarch64 -machine virt,virtualization=on -cpu cortex-a57 -nographic -m 1024 -kernel "${BUILD_DIR}/novavisor.elf"
}

cmd_ci() {
    echo "==> Running Local CI Pipeline..."
    cmd_format

    # Check if format modified any files
    if ! git diff --quiet; then
        echo "Error: CI Failed. 'clang-format' modified files. Please commit the formatted files."
        exit 1
    fi

    cmd_build --release
    cmd_lint
    echo "==> Local CI Pipeline Passed Successfully!"
}

# ------------------------------------------------------------------------------
# Main Dispatcher
# ------------------------------------------------------------------------------

if [ $# -eq 0 ]; then
    print_usage
    exit 1
fi

SUBCOMMAND="$1"
shift

case "${SUBCOMMAND}" in
    build)  cmd_build "$@" ;;
    clean)  cmd_clean "$@" ;;
    format) cmd_format "$@" ;;
    lint)   cmd_lint "$@" ;;
    run)    cmd_run "$@" ;;
    ci)     cmd_ci "$@" ;;
    -h|--help) print_usage ;;
    *)
        echo "Error: Unknown subcommand '${SUBCOMMAND}'"
        print_usage
        exit 1
        ;;
esac
