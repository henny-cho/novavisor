#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/versions.sh disable=SC1091
source "${WORK_DIR}/scripts/lib/versions.sh"
if [[ -d "${WORK_DIR}/.toolchain/current/bin" ]]; then
    export PATH="${WORK_DIR}/.toolchain/current/bin:${PATH}"
fi

if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "Usage: $0 <novavisor.bin> [output-directory]" >&2
    exit 2
fi

PAYLOAD="$(realpath "$1")"
OUTPUT_DIR="${2:-${WORK_DIR}/build/n1sdp-firmware}"
mkdir -p "${OUTPUT_DIR}"
OUTPUT_DIR="$(cd "${OUTPUT_DIR}" && pwd -P)"
SOURCE_DIR="${WORK_DIR}/external/cache/firmware/arm-trusted-firmware-${N1SDP_TFA_VERSION}"
BUILD_BASE="${OUTPUT_DIR}/tf-a-build"

if [[ ! -f "${PAYLOAD}" ]]; then
    echo "Error: BL33 payload not found: ${PAYLOAD}" >&2
    exit 1
fi
if ! command -v aarch64-none-elf-gcc >/dev/null 2>&1; then
    echo "Error: aarch64-none-elf toolchain is not available" >&2
    exit 1
fi

mkdir -p "$(dirname "${SOURCE_DIR}")"
if [[ ! -d "${SOURCE_DIR}/.git" ]]; then
    git clone --filter=blob:none --no-checkout \
        https://github.com/ARM-software/arm-trusted-firmware.git "${SOURCE_DIR}"
fi
git -C "${SOURCE_DIR}" fetch --depth=1 origin "${N1SDP_TFA_COMMIT}"
git -C "${SOURCE_DIR}" checkout --detach "${N1SDP_TFA_COMMIT}"
if [[ "$(git -C "${SOURCE_DIR}" rev-parse HEAD)" != "${N1SDP_TFA_COMMIT}" ]]; then
    echo "Error: Trusted Firmware-A revision verification failed" >&2
    exit 1
fi

make -C "${SOURCE_DIR}" \
    BUILD_BASE="${BUILD_BASE}" \
    CROSS_COMPILE=aarch64-none-elf- \
    PLAT=n1sdp \
    DEBUG=0 \
    BL33="${PAYLOAD}" \
    fip

FIP="${BUILD_BASE}/n1sdp/release/fip.bin"
FIPTOOL="${BUILD_BASE}/n1sdp/release/tools/fiptool/fiptool"
"${FIPTOOL}" update --nt-fw "${PAYLOAD}" "${FIP}"
VERIFY_PAYLOAD="$(mktemp)"
trap 'rm -f "${VERIFY_PAYLOAD}"' EXIT
"${FIPTOOL}" unpack --force --nt-fw "${VERIFY_PAYLOAD}" "${FIP}"
if ! cmp -s "${PAYLOAD}" "${VERIFY_PAYLOAD}"; then
    echo "Error: packaged BL33 does not match the linked image" >&2
    exit 1
fi

cmake -E copy_if_different \
    "${FIP}" \
    "${OUTPUT_DIR}/fip.bin"

echo "N1SDP firmware package: ${OUTPUT_DIR}/fip.bin"
