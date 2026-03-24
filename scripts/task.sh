#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# NovaVisor Task Runner
# Command central for development cycle, CI, and repetitive tasks.
# ==============================================================================

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
    echo "  format      Run clang-format on source files (--check for dry-run)"
    echo "  lint        Run clang-tidy static analysis"
    echo "  run         Run novavisor.elf in QEMU"
    echo "  ci          Run the full CI pipeline (format check + build + lint + test)"
    echo "  test        Build and run host GTest suite (x86_64, no toolchain)"
    echo ""
    echo "Options:"
    echo "  --release   Build in Release mode (default: Debug)"
    echo "  --clean     (build/lint) Remove the build directory first"
    echo "  --check     (format only) Verify formatting without modifying files"
    echo "  -h, --help  Show this help message"
}

# ------------------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------------------

# Enumerate all project C/C++ source and header files, excluding generated
# and vendored directories. Single source of truth for format and lint targets.
_find_sources() {
    find . \
        -type d \( -name "external" -o -name ".toolchain" -o -name "build" \) -prune \
        -o -type f \( -name '*.cpp' -o -name '*.hpp' -o -name '*.c' -o -name '*.h' \) -print
}

# Set up the project-local CPM package cache and export CPM_SOURCE_CACHE.
# Must be called before any cmake invocation.
_setup_cpm_cache() {
    local CPM_VER="0.42.1"
    local CPM_CACHE_DIR="${WORK_DIR}/external/cache/cpm"
    local CPM_CACHE_FILE="${CPM_CACHE_DIR}/CPM_${CPM_VER}.cmake"
    mkdir -p "${CPM_CACHE_DIR}"
    # Bootstrap the cache from an existing build directory on first run.
    if [ ! -f "${CPM_CACHE_FILE}" ] && [ -f "${BUILD_DIR}/cmake/CPM_${CPM_VER}.cmake" ]; then
        cp "${BUILD_DIR}/cmake/CPM_${CPM_VER}.cmake" "${CPM_CACHE_FILE}"
    fi
    export CPM_SOURCE_CACHE="${WORK_DIR}/external/cache"
}

# Parse a --release flag from "$@" and echo the resulting BUILD_TYPE.
# Callers: local BUILD_TYPE; BUILD_TYPE=$(_parse_build_type "$@")
_parse_build_type() {
    local type="Debug"
    for arg in "$@"; do
        [[ "${arg}" == "--release" ]] && type="Release"
    done
    echo "${type}"
}

# ------------------------------------------------------------------------------
# Subcommand implementations
# ------------------------------------------------------------------------------

cmd_build() {
    local BUILD_TYPE
    BUILD_TYPE=$(_parse_build_type "$@")

    local CLEAN=0
    for arg in "$@"; do
        [[ "${arg}" == "--clean" ]] && CLEAN=1
    done

    if [[ ${CLEAN} -eq 1 ]]; then
        cmd_clean
    fi

    _setup_cpm_cache

    local PRESET="aarch64-debug"
    [[ "${BUILD_TYPE}" == "Release" ]] && PRESET="aarch64-release"

    echo "==> Configuring CMake with preset: ${PRESET}..."
    cmake --preset "${PRESET}"

    echo "==> Building..."
    cmake --build --preset "${PRESET}"
}

cmd_clean() {
    echo "==> Cleaning build directory..."
    rm -rf "${BUILD_DIR}"
    echo "Clean complete."
}

cmd_format() {
    local CHECK_ONLY=0
    for arg in "$@"; do
        [[ "${arg}" == "--check" ]] && CHECK_ONLY=1
    done

    if [[ ${CHECK_ONLY} -eq 1 ]]; then
        echo "==> Checking clang-format compliance..."
        local violations
        violations=$(_find_sources | xargs -r clang-format --dry-run --Werror 2>&1 || true)
        if [[ -n "${violations}" ]]; then
            echo "Error: formatting violations found:"
            echo "${violations}"
            echo "Run 'scripts/task.sh format' to fix."
            exit 1
        fi
        echo "Format check passed."
    else
        echo "==> Running clang-format..."
        _find_sources | xargs -r clang-format -i
        echo "Formatting complete."
    fi
}

cmd_lint() {
    local BUILD_TYPE
    BUILD_TYPE=$(_parse_build_type "$@")

    local CLEAN=0
    for arg in "$@"; do
        [[ "${arg}" == "--clean" ]] && CLEAN=1
    done

    if [[ ${CLEAN} -eq 1 ]]; then
        cmd_clean
    fi

    local PRESET="aarch64-lint"

    _setup_cpm_cache

    echo "==> Running clang-tidy with preset: ${PRESET}..."
    # If BUILD_TYPE matches the previous cmake configure, ninja reuses object
    # files and only the clang-tidy pass runs -- no full recompile.
    cmake --preset "${PRESET}"
    cmake --build --preset "${PRESET}"
    echo "Linting complete."
}

cmd_run() {
    echo "==> Running NovaVisor in QEMU..."
    echo "==> Press Ctrl-A then x to exit QEMU."
    if [ ! -f "${BUILD_DIR}/novavisor.elf" ]; then
        echo "Executable not found. Building first..."
        cmd_build
    fi
    qemu-system-aarch64 \
        -machine virt,virtualization=on \
        -cpu cortex-a57 \
        -nographic \
        -m 1024 \
        -kernel "${BUILD_DIR}/novavisor.elf"
}

cmd_test() {
    local PRESET="host-debug"

    _setup_cpm_cache

    echo "==> Configuring host GTest build with preset: ${PRESET}..."
    cmake --preset "${PRESET}"

    echo "==> Building host tests..."
    cmake --build --preset "${PRESET}"

    echo "==> Running host tests..."
    ctest --preset "${PRESET}" --output-on-failure
}

cmd_ci() {
    echo "==> Running Local CI Pipeline..."
    # Check only -- do not modify files. CI must fail on unformatted code,
    # not silently reformat it. Run 'task.sh format' locally before committing.
    cmd_format --check
    cmd_build --release
    cmd_lint --release
    cmd_test
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
    build)     cmd_build  "$@" ;;
    clean)     cmd_clean  "$@" ;;
    format)    cmd_format "$@" ;;
    lint)      cmd_lint   "$@" ;;
    run)       cmd_run    "$@" ;;
    test)      cmd_test   "$@" ;;
    ci)        cmd_ci     "$@" ;;
    -h|--help) print_usage ;;
    *)
        echo "Error: Unknown subcommand '${SUBCOMMAND}'"
        print_usage
        exit 1
        ;;
esac
