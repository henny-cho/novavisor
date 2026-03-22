#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# NovaVisor Environment Setup Script
# ==============================================================================
# This script sets up the local development environment for building the
# NovaVisor bare-metal hypervisor.
# It downloads the official ARM GCC toolchain and installs required dependencies.
# ==============================================================================

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."; pwd)"
TOOLCHAIN_DIR="${WORK_DIR}/.toolchain"
# Load globally defined versions
source "$(dirname "$0")/versions.sh"
EXTRACT_DIR="${TOOLCHAIN_DIR}/${TOOLCHAIN_EXTRACT_NAME}"

echo "=== NovaVisor Environment Setup ==="

# 1. Install host dependencies (Ubuntu/Debian based)
echo "[1] Checking and installing host dependencies..."
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y \
        build-essential \
        cmake \
        ninja-build \
        wget \
        xz-utils \
        qemu-system-arm \
        python3 \
        python3-pip \
        device-tree-compiler \
        clang-format \
        clang-tidy \
        pre-commit
else
    echo "Warning: apt-get not found. Please ensure dependencies like cmake, ninja, qemu are installed manually."
fi

# 2. Setup ARM GCC Toolchain
echo "[2] Setting up ARM GCC aarch64-none-elf toolchain..."
mkdir -p "${TOOLCHAIN_DIR}"

if [ ! -d "${EXTRACT_DIR}" ]; then
    echo "Downloading ${TOOLCHAIN_TAR}..."
    wget -q --show-progress -c "${TOOLCHAIN_URL}" -O "${TOOLCHAIN_DIR}/${TOOLCHAIN_TAR}"

    echo "Extracting toolchain (this may take a moment)..."
    tar -xf "${TOOLCHAIN_DIR}/${TOOLCHAIN_TAR}" -C "${TOOLCHAIN_DIR}"

    echo "Cleaning up tarball..."
    rm -f "${TOOLCHAIN_DIR}/${TOOLCHAIN_TAR}"

    echo "Creating symlink to 'current'..."
    ln -sfn "${EXTRACT_DIR}" "${TOOLCHAIN_DIR}/current"
else
    echo "Toolchain already exists at ${EXTRACT_DIR}"
    ln -sfn "${EXTRACT_DIR}" "${TOOLCHAIN_DIR}/current"
fi

# 3. Verify env.sh exists (managed in version control, not generated)
ENV_SCRIPT="${TOOLCHAIN_DIR}/env.sh"
if [ ! -f "${ENV_SCRIPT}" ]; then
    echo "[3] env.sh not found. Generating from template..."
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
    echo "[3] env.sh generated at ${ENV_SCRIPT}"
else
    echo "[3] env.sh found at ${ENV_SCRIPT}"
fi

# 4. Install pre-commit hooks (step numbering preserved)
echo "[4] Installing git pre-commit hooks..."
if [ -d "${WORK_DIR}/.git" ] && command -v pre-commit >/dev/null 2>&1; then
    pre-commit install
else
    echo "Warning: .git directory not found or pre-commit not installed. Skipping git hook installation."
fi

echo "=============================================================================="
echo "Setup Complete!"
echo "Run the following command to load the toolchain into your current session:"
echo "  source ${TOOLCHAIN_DIR}/env.sh"
echo "=============================================================================="
