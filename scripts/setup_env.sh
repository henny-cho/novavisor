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
    clang-tidy
    pipx
    qemu-system-aarch64
    python3
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
    sudo apt-get update -q
    sudo apt-get install -y "${packages[@]}"
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

# task.sh sources .toolchain/env.sh at startup; generate it if missing.
ensure_env_script() {
    local ENV_SCRIPT="${TOOLCHAIN_DIR}/env.sh"
    if [ -f "${ENV_SCRIPT}" ]; then
        echo "==> env.sh found at ${ENV_SCRIPT}"
        return
    fi
    echo "==> Generating ${ENV_SCRIPT}..."
    cat > "${ENV_SCRIPT}" <<'EOF'
#!/usr/bin/env bash
# Source this file to add the custom ARM toolchain to your PATH
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
ensure_env_script
if [[ ${CI_MODE} -eq 0 ]]; then
    install_hooks
fi

echo "=== Setup complete ==="
if [[ ${CI_MODE} -eq 0 ]]; then
    echo "Load the toolchain into your current shell:"
    echo "  source ${TOOLCHAIN_DIR}/env.sh"
fi
