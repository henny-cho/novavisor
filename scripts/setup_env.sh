#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# NovaVisor Environment Setup
# ==============================================================================
# Installs the apt packages and the ARM GNU aarch64-none-elf toolchain needed
# to build, lint, test, and run NovaVisor. Single source of truth for host
# dependencies, shared by developer machines and CI.
#
# Usage:
#   scripts/setup_env.sh          Full developer setup: core + dev packages,
#                                 toolchain, git pre-commit hooks.
#   scripts/setup_env.sh --ci     CI subset: core packages + toolchain only.
# ==============================================================================

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLCHAIN_DIR="${WORK_DIR}/.toolchain"

# Exports TOOLCHAIN_URL / TOOLCHAIN_TAR / TOOLCHAIN_EXTRACT_NAME used below.
# shellcheck source=versions.sh disable=SC1091
source "${WORK_DIR}/scripts/versions.sh"
EXTRACT_DIR="${TOOLCHAIN_DIR}/${TOOLCHAIN_EXTRACT_NAME}"

CI_MODE=0
case "${1:-}" in
    --ci) CI_MODE=1 ;;
    "") ;;
    *)
        echo "Usage: $0 [--ci]" >&2
        exit 2
        ;;
esac

# Everything scripts/task.sh needs for build / lint / test / demo.
CORE_PACKAGES=(
    build-essential
    cmake
    ninja-build
    ccache
    "clang-tidy-${CLANG_TIDY_VERSION}"
    curl
    git
    pipx
    qemu-system-arm
    python3
    python3-venv
    python3-yaml
    python3-pexpect
    wget
    xz-utils
    # Linux-userspace cross toolchain + kernel-build tools for the
    # external guest images (demo fetch): the bare-metal GCC has no
    # libc, and the kernel's kconfig/scripts need flex/bison/bc.
    gcc-aarch64-linux-gnu
    flex
    bison
    bc
    libssl-dev
)

# Developer-workstation extras, not needed by CI (mirrors
# .devcontainer/Dockerfile): pre-commit hook stack and DTB tooling for
# roadmap Phase 12.
DEV_PACKAGES=(
    shellcheck
    pre-commit
    device-tree-compiler
)

install_packages() {
    echo "==> Installing host packages..."
    if ! command -v apt-get >/dev/null 2>&1; then
        echo "Warning: apt-get not found. Please install manually:"
        echo "  ${CORE_PACKAGES[*]}"
        return
    fi
    local packages=("${CORE_PACKAGES[@]}")
    if [[ ${CI_MODE} -eq 0 ]]; then
        packages+=("${DEV_PACKAGES[@]}")
    fi
    local privilege=()
    if [[ ${EUID} -ne 0 ]]; then
        privilege=(sudo)
    fi
    "${privilege[@]}" apt-get update -q
    "${privilege[@]}" apt-get install -y "${packages[@]}"
}

# The distro clang-format drifts across releases (v18 on noble) and majors
# disagree on wrapping rules, so install the pinned version instead of the
# apt package. task.sh format enforces the pinned major at runtime.
install_clang_format() {
    echo "==> Installing clang-format ${CLANG_FORMAT_VERSION}..."
    pipx install --force "clang-format==${CLANG_FORMAT_VERSION}"
}

install_toolchain() {
    echo "==> Setting up ARM GNU aarch64-none-elf toolchain..."
    mkdir -p "${TOOLCHAIN_DIR}"
    if [ -d "${EXTRACT_DIR}" ]; then
        echo "Toolchain already present at ${EXTRACT_DIR}"
    else
        # shellcheck disable=SC2153  # TOOLCHAIN_TAR is sourced from versions.sh
        echo "Downloading ${TOOLCHAIN_TAR}..."
        wget -q --show-progress -c "${TOOLCHAIN_URL}" -O "${TOOLCHAIN_DIR}/${TOOLCHAIN_TAR}"
        echo "Extracting toolchain (this may take a moment)..."
        tar -xf "${TOOLCHAIN_DIR}/${TOOLCHAIN_TAR}" -C "${TOOLCHAIN_DIR}"
        rm -f "${TOOLCHAIN_DIR}/${TOOLCHAIN_TAR}"
    fi
    ln -sfn "${EXTRACT_DIR}" "${TOOLCHAIN_DIR}/current"
}

qemu_version() {
    "$1" --version 2>/dev/null |
        sed -nE 's/.*version ([0-9]+(\.[0-9]+)+).*/\1/p' |
        head -n 1
}

qemu_is_compatible() {
    local binary="$1"
    local version
    [ -x "${binary}" ] || return 1
    version="$(qemu_version "${binary}")"
    [ -n "${version}" ] || return 1
    python3 - "${version}" "${QEMU_MIN_VERSION}" <<'PY'
import re
import sys

def version(value):
    return tuple(int(part) for part in re.findall(r"\d+", value))

raise SystemExit(0 if version(sys.argv[1]) >= version(sys.argv[2]) else 1)
PY
}

check_qemu() {
    local system_qemu
    system_qemu="$(command -v qemu-system-aarch64 || true)"

    if [ -n "${system_qemu}" ] && qemu_is_compatible "${system_qemu}"; then
        echo "==> Compatible QEMU $(qemu_version "${system_qemu}") found at ${system_qemu}"
        return
    fi

    local found="not found"
    if [ -n "${system_qemu}" ]; then
        found="$(qemu_version "${system_qemu}")"
    fi
    echo "Error: QEMU ${QEMU_MIN_VERSION}+ is required for nested SMMUv3; found ${found}." >&2
    echo "Use an OS package source that provides QEMU ${QEMU_MIN_VERSION} or newer." >&2
    return 1
}

# task.sh sources .toolchain/env.sh at startup.
ensure_env_script() {
    local ENV_SCRIPT="${TOOLCHAIN_DIR}/env.sh"
    echo "==> Generating ${ENV_SCRIPT}..."
    cat > "${ENV_SCRIPT}" <<'EOF'
#!/usr/bin/env bash
# Source this file to add project-pinned tools to PATH.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="${PROJECT_ROOT}/.toolchain/current/bin:${PATH}"
export CROSS_COMPILE="aarch64-none-elf-"
echo "NovaVisor environment loaded! Toolchain in use:"
aarch64-none-elf-gcc --version | head -n 1
EOF
    chmod +x "${ENV_SCRIPT}"
}

install_hooks() {
    echo "==> Installing git pre-commit hooks..."
    if [ -d "${WORK_DIR}/.git" ] && command -v pre-commit >/dev/null 2>&1; then
        (cd "${WORK_DIR}" && pre-commit install)
    else
        echo "Warning: .git not found or pre-commit not installed. Skipping hooks."
    fi
}

echo "=== NovaVisor Environment Setup ==="
install_packages
install_clang_format
install_toolchain
check_qemu
ensure_env_script
if [[ ${CI_MODE} -eq 0 ]]; then
    install_hooks
fi

echo "=== Setup complete ==="
if [[ ${CI_MODE} -eq 0 ]]; then
    echo "Load the toolchain into your current shell:"
    echo "  source ${TOOLCHAIN_DIR}/env.sh"
fi
