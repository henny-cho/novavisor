#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# NovaVisor Task Runner
# Command central for development cycle, CI, and repetitive tasks.
# ==============================================================================

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ROOT="${WORK_DIR}/build"

# Pinned tool versions (CLANG_FORMAT_VERSION, toolchain).
# shellcheck source=versions.sh disable=SC1091
source "${WORK_DIR}/scripts/versions.sh"

# Resolve a CMake preset's binaryDir. Presets use ${sourceDir}/build/<name>,
# so this is a simple mapping — kept as a function so callers never hardcode
# the layout.
_preset_dir() {
    echo "${BUILD_ROOT}/$1"
}

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
    echo "  lint        Run clang-tidy (run-clang-tidy over the debug compile database)"
    echo "  run         Run novavisor.elf in QEMU"
    echo "  debug       Run QEMU with -s -S (GDB server on :1234, halted at reset)"
    echo "  size        Print section sizes of novavisor.elf"
    echo "  objdump     Disassemble novavisor.elf (interleaved with source)"
    echo "  ci          Run the full CI pipeline (format check + build + lint + test + demo)"
    echo "  test        Build and run host GTest suite (x86_64, no toolchain)"
    echo "  demo        Manage demo guests (list | run <id> | verify <id> | verify-all | debug <id>)"
    echo ""
    echo "Options:"
    echo "  --release   Build in Release mode (default: Debug)"
    echo "  --clean     (build only) Remove the build directory first"
    echo "  --config F  (build/run) Guest config YAML (default: configs/default.yml)"
    echo "  --check     (format only) Verify formatting without modifying files"
    echo "  -h, --help  Show this help message"
}

# ------------------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------------------

# Enumerate all project C/C++ source and header files, excluding generated
# and vendored directories. NUL-delimited; pipe into `xargs -0`.
# Single source of truth for format and lint targets.
_find_sources() {
    find . \
        -type d \( -name "external" -o -name ".toolchain" -o -name "build" \) -prune \
        -o -type f \( -name '*.cpp' -o -name '*.hpp' -o -name '*.c' -o -name '*.h' \) -print0
}

# Resolve a clang-format binary matching the pinned major version. Majors
# disagree on wrapping rules, so a mismatched binary reports false
# violations against (or silently rewrites) CI-canonical formatting.
_clang_format() {
    local major="${CLANG_FORMAT_VERSION%%.*}"
    local bin
    for bin in "clang-format-${major}" clang-format; do
        command -v "${bin}" >/dev/null 2>&1 || continue
        if "${bin}" --version | grep -q " ${major}\."; then
            echo "${bin}"
            return 0
        fi
    done
    echo "Error: clang-format ${major}.x not found (pinned: ${CLANG_FORMAT_VERSION})." >&2
    echo "Install it with: pipx install clang-format==${CLANG_FORMAT_VERSION}" >&2
    return 1
}

# Route CPM/FetchContent source checkouts to a project-local cache shared by
# all build trees. cmake/get_cpm.cmake downloads CPM itself into the same
# cache (hash-verified). Must be called before any cmake invocation.
_setup_cpm_cache() {
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

# Parse a `--config <file>` option from "$@" and echo the file (empty
# when absent). Callers: local CONFIG; CONFIG=$(_parse_config "$@")
_parse_config() {
    local expect=0
    for arg in "$@"; do
        if [[ ${expect} -eq 1 ]]; then
            echo "${arg}"
            return 0
        fi
        [[ "${arg}" == "--config" ]] && expect=1
    done
    if [[ ${expect} -eq 1 ]]; then
        echo "Error: --config requires a file argument" >&2
        exit 2
    fi
}

# Point the build tree at a guest config (default: configs/default.yml).
# Copies only on content change, so Ninja regenerates the guest DTBs
# exactly when the config differs — no CMake reconfigure involved (the
# DTB pipeline DEPENDS on active_config.yml). Omitting --config always
# restores the default, so a config never lingers across builds.
_sync_config() {
    local preset_dir="$1"
    local config="${2:-${WORK_DIR}/configs/default.yml}"
    if [ ! -f "${config}" ]; then
        echo "Error: guest config not found: ${config}" >&2
        exit 1
    fi
    mkdir -p "${preset_dir}"
    if ! cmp -s "${config}" "${preset_dir}/active_config.yml"; then
        cp "${config}" "${preset_dir}/active_config.yml"
    fi
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

    local CONFIG
    CONFIG=$(_parse_config "$@")
    _sync_config "$(_preset_dir "${PRESET}")" "${CONFIG}"

    # Configure only on the first run; afterwards Ninja re-runs CMake by
    # itself whenever a CMakeLists.txt changes, so repeat builds stay fast.
    if [ ! -f "$(_preset_dir "${PRESET}")/build.ninja" ]; then
        echo "==> Configuring CMake with preset: ${PRESET}..."
        cmake --preset "${PRESET}"
    fi

    echo "==> Building (${PRESET})..."
    cmake --build --preset "${PRESET}"
}

cmd_clean() {
    echo "==> Cleaning build directory..."
    rm -rf "${BUILD_ROOT}"
    echo "Clean complete."
}

cmd_format() {
    local CHECK_ONLY=0
    for arg in "$@"; do
        [[ "${arg}" == "--check" ]] && CHECK_ONLY=1
    done

    local fmt
    fmt="$(_clang_format)"

    if [[ ${CHECK_ONLY} -eq 1 ]]; then
        echo "==> Checking clang-format compliance (${fmt})..."
        local violations
        violations=$(_find_sources | xargs -0 -r "${fmt}" --dry-run --Werror 2>&1 || true)
        if [[ -n "${violations}" ]]; then
            echo "Error: formatting violations found:"
            echo "${violations}"
            echo "Run 'scripts/task.sh format' to fix."
            exit 1
        fi
        echo "Format check passed."
    else
        echo "==> Running clang-format (${fmt})..."
        _find_sources | xargs -0 -r "${fmt}" -i
        echo "Formatting complete."
    fi
}

cmd_lint() {
    # Lint reuses the debug tree's compile_commands.json — no dedicated lint
    # build tree, no recompile beyond a normal incremental debug build.
    local PRESET="aarch64-debug"
    local BUILD_DIR
    BUILD_DIR="$(_preset_dir "${PRESET}")"

    _setup_cpm_cache

    if [ ! -f "${BUILD_DIR}/clang_tidy_extra_args.txt" ]; then
        echo "==> Configuring CMake with preset: ${PRESET}..."
        cmake --preset "${PRESET}"
    fi
    # Building keeps compile_commands.json fresh: Ninja re-runs CMake when
    # any CMakeLists.txt changed, and a no-change rebuild is nearly free.
    cmake --build --preset "${PRESET}"

    echo "==> Running run-clang-tidy over the ${PRESET} compile database..."
    # clang-tidy replays GCC compile commands with Clang's driver; the args
    # file (written at configure time by cmake/clang_tidy.cmake) retargets it
    # to aarch64-none-elf and injects GCC's implicit include dirs.
    local EXTRA_ARGS=()
    mapfile -t EXTRA_ARGS < "${BUILD_DIR}/clang_tidy_extra_args.txt"
    # .cpp only: the compile database also lists .S assembly TUs, which
    # clang-tidy cannot parse.
    # -header-filter is passed explicitly because clang-tidy 19+ no longer
    # honors HeaderFilterRegex from .clang-tidy — relying on the config file
    # silently skips all header diagnostics on newer versions.
    run-clang-tidy -quiet -p "${BUILD_DIR}" "${EXTRA_ARGS[@]}" \
        '-header-filter=/(components|hal|nova|projects)/' \
        "^${WORK_DIR}/(components|hal|nova|projects)/.*\.cpp\$"
    echo "Linting complete."
}

cmd_run() {
    # Build first so the image always matches --config/--release —
    # a no-change Ninja rebuild is nearly free.
    cmd_build "$@" >&2
    local ELF
    ELF=$(_resolve_elf "$@")

    echo "==> Running NovaVisor in QEMU: ${ELF}"
    echo "==> Press Ctrl-A then x to exit QEMU."
    qemu-system-aarch64 \
        -machine virt,virtualization=on,gic-version=3 \
        -cpu cortex-a57 \
        -smp 2 \
        -nographic \
        -m 1024 \
        -kernel "${ELF}"
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

# Resolve the ELF path for the current build flavor, preferring Release if both
# exist. Used by debug/size/objdump which don't care which flavor ran last.
_resolve_elf() {
    local BUILD_TYPE
    BUILD_TYPE=$(_parse_build_type "$@")
    local PRESET="aarch64-debug"
    [[ "${BUILD_TYPE}" == "Release" ]] && PRESET="aarch64-release"

    local ELF
    ELF="$(_preset_dir "${PRESET}")/novavisor.elf"
    if [ ! -f "${ELF}" ]; then
        echo "${ELF} not found. Building first..." >&2
        cmd_build "$@" >&2
    fi
    echo "${ELF}"
}

cmd_debug() {
    local ELF
    ELF=$(_resolve_elf "$@")
    echo "==> Launching QEMU with GDB stub on :1234 (CPU halted)."
    echo "==> In another shell:  aarch64-none-elf-gdb ${ELF} -ex 'target remote :1234'"
    echo "==> Press Ctrl-A then x in QEMU to exit."
    qemu-system-aarch64 \
        -machine virt,virtualization=on,gic-version=3 \
        -cpu cortex-a57 \
        -smp 2 \
        -nographic \
        -m 1024 \
        -kernel "${ELF}" \
        -s -S
}

cmd_size() {
    local ELF
    ELF=$(_resolve_elf "$@")
    aarch64-none-elf-size "${ELF}"
}

cmd_objdump() {
    local ELF
    ELF=$(_resolve_elf "$@")
    # -d disassemble, -S interleave source (requires -g build), -C demangle.
    aarch64-none-elf-objdump -d -S -C "${ELF}"
}

cmd_demo() {
    # Delegate all demo operations to the Python harness. The harness
    # handles: manifest parsing, hypervisor + demo guest builds, QEMU
    # invocation, and pexpect-based pattern verification.
    local sub="${1:-list}"
    shift || true
    case "${sub}" in
        list|run|verify|verify-all|debug)
            # -u: unbuffered stdout so VS Code's background problem matcher
            # sees the ==> markers emitted by `demo debug` before QEMU
            # replaces the process and output sits in a 4K block buffer.
            python3 -u "${WORK_DIR}/scripts/demo_runner.py" "${sub}" "$@"
            ;;
        *)
            echo "Error: unknown demo subcommand '${sub}'" >&2
            echo "Usage: $0 demo {list|run|verify|verify-all|debug} [id|name]" >&2
            exit 2
            ;;
    esac
}

cmd_ci() {
    echo "==> Running Local CI Pipeline..."
    # Format check runs in parallel with release build (pure-CPU vs I/O).
    # Lint and test are serialized after because they each trigger
    # FetchContent/CPM configure; concurrent CPM cache writes can race.
    cmd_format --check &
    local FORMAT_PID=$!
    cmd_build --release &
    local BUILD_PID=$!

    if ! wait "${FORMAT_PID}"; then
        kill "${BUILD_PID}" 2>/dev/null || true
        wait "${BUILD_PID}" 2>/dev/null || true
        echo "==> CI aborted: format check failed" >&2
        exit 1
    fi
    if ! wait "${BUILD_PID}"; then
        echo "==> CI aborted: build failed" >&2
        exit 1
    fi

    cmd_lint
    cmd_test
    # Run all enabled demos. Before any phase 5+ lands, this is a no-op
    # that prints "no enabled demos" and exits 0, so it's safe to include
    # in CI from day one.
    cmd_demo verify-all
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
    build)     cmd_build   "$@" ;;
    clean)     cmd_clean   "$@" ;;
    format)    cmd_format  "$@" ;;
    lint)      cmd_lint    "$@" ;;
    run)       cmd_run     "$@" ;;
    debug)     cmd_debug   "$@" ;;
    size)      cmd_size    "$@" ;;
    objdump)   cmd_objdump "$@" ;;
    test)      cmd_test    "$@" ;;
    demo)      cmd_demo    "$@" ;;
    ci)        cmd_ci      "$@" ;;
    -h|--help) print_usage ;;
    *)
        echo "Error: Unknown subcommand '${SUBCOMMAND}'"
        print_usage
        exit 1
        ;;
esac
