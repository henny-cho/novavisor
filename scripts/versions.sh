#!/usr/bin/env bash

# ARM GNU Toolchain Version
export TOOLCHAIN_VERSION="15.2.Rel1"

# Dynamically detect host OS and CPU Architecture
HOST_OS="$(uname -s)"
HOST_ARCH="$(uname -m)"

if [ "${HOST_OS}" = "Darwin" ]; then
    OS_PREFIX="darwin-"
    if [ "${HOST_ARCH}" = "arm64" ] || [ "${HOST_ARCH}" = "aarch64" ]; then
        ARCH_SUFFIX="arm64"
    else
        ARCH_SUFFIX="x86_64"
    fi
else
    # Linux or Windows Subsystem
    OS_PREFIX=""
    if [ "${HOST_ARCH}" = "arm64" ] || [ "${HOST_ARCH}" = "aarch64" ]; then
        ARCH_SUFFIX="aarch64"
    else
        ARCH_SUFFIX="x86_64"
    fi
fi

export TOOLCHAIN_TAR_NAME="arm-gnu-toolchain-${TOOLCHAIN_VERSION}-${OS_PREFIX}${ARCH_SUFFIX}-aarch64-none-elf"
export TOOLCHAIN_EXTRACT_NAME="arm-gnu-toolchain-${TOOLCHAIN_VERSION,,}-${OS_PREFIX}${ARCH_SUFFIX}-aarch64-none-elf"
export TOOLCHAIN_TAR="${TOOLCHAIN_TAR_NAME}.tar.xz"
export TOOLCHAIN_URL="https://developer.arm.com/-/media/Files/downloads/gnu/${TOOLCHAIN_VERSION}/binrel/${TOOLCHAIN_TAR}"
