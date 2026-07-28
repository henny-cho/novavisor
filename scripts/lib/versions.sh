#!/usr/bin/env bash

VERSION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../tool-versions.env disable=SC1091
source "${VERSION_DIR}/tool-versions.env"

HOST_OS="$(uname -s)"
HOST_ARCH="$(uname -m)"
OS_PREFIX=""
ARCH_SUFFIX="x86_64"
if [[ "${HOST_OS}" == "Darwin" ]]; then
    OS_PREFIX="darwin-"
fi
if [[ "${HOST_ARCH}" == "arm64" || "${HOST_ARCH}" == "aarch64" ]]; then
    ARCH_SUFFIX="$([[ "${HOST_OS}" == "Darwin" ]] && echo arm64 || echo aarch64)"
fi

export TOOLCHAIN_VERSION="${ARM_GNU_VERSION}"
export TOOLCHAIN_TAR_NAME="arm-gnu-toolchain-${ARM_GNU_VERSION}-${OS_PREFIX}${ARCH_SUFFIX}-aarch64-none-elf"
export TOOLCHAIN_EXTRACT_NAME="arm-gnu-toolchain-${ARM_GNU_VERSION,,}-${OS_PREFIX}${ARCH_SUFFIX}-aarch64-none-elf"
export TOOLCHAIN_TAR="${TOOLCHAIN_TAR_NAME}.tar.xz"
export TOOLCHAIN_URL="https://developer.arm.com/-/media/Files/downloads/gnu/${ARM_GNU_VERSION}/binrel/${TOOLCHAIN_TAR}"
export CLANG_FORMAT_VERSION CLANG_TIDY_VERSION QEMU_MIN_VERSION
export TFA_VERSION TFA_COMMIT RUFF_VERSION ACTIONLINT_VERSION
