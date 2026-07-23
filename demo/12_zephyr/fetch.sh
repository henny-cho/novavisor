#!/usr/bin/env bash
# Build the pinned Zephyr heartbeat image (RAM relocated via
# novavisor.overlay) and populate
# external/cache/guests/12_zephyr/. Idempotent: a cache hit with a
# matching version stamp performs zero network access and no build.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ZEPHYR_TAG="v4.4.1"
IMAGE_VERSION="${ZEPHYR_TAG}-heartbeat-v1"
ZEPHYR_URL="https://github.com/zephyrproject-rtos/zephyr.git"
BOARD="qemu_cortex_a53"
APP="${DEMO_DIR}/app"

WORKSPACE="${REPO}/external/zephyr"
VENV="${WORKSPACE}/.venv"
CACHE="${REPO}/external/cache/guests/12_zephyr"
STAMP="${CACHE}/zephyr.version"

if [[ -f "${STAMP}" && "$(cat "${STAMP}")" == "${IMAGE_VERSION}" &&
      -f "${CACHE}/zephyr.bin" && -f "${CACHE}/zephyr.elf" ]]; then
    echo "==> Zephyr ${IMAGE_VERSION} already cached (${CACHE})"
    exit 0
fi

# Same cross GCC the hypervisor uses — no Zephyr SDK download. The
# cross-compile variant wants an absolute prefix.
source "${REPO}/.toolchain/env.sh"
export ZEPHYR_TOOLCHAIN_VARIANT=cross-compile
export CROSS_COMPILE="${REPO}/.toolchain/current/bin/aarch64-none-elf-"

mkdir -p "${WORKSPACE}"

# Isolated python env: west + Zephyr's build-time deps.
if [[ ! -x "${VENV}/bin/west" ]]; then
    python3 -m venv "${VENV}"
    "${VENV}/bin/pip" install --quiet west
fi
export PATH="${VENV}/bin:${PATH}"

# Manifest repo only, shallow at the pinned tag. The project filter
# drops every module: an arm64 QEMU board needs none, and this keeps
# the multi-GB west workspace down to the Zephyr tree itself.
if [[ ! -d "${WORKSPACE}/zephyr/.git" ]]; then
    git clone --depth 1 --branch "${ZEPHYR_TAG}" "${ZEPHYR_URL}" "${WORKSPACE}/zephyr"
fi
if [[ ! -d "${WORKSPACE}/.west" ]]; then
    (cd "${WORKSPACE}" && west init -l zephyr)
    (cd "${WORKSPACE}" && west config manifest.project-filter -- "-.*")
fi
"${VENV}/bin/pip" install --quiet -r "${WORKSPACE}/zephyr/scripts/requirements-base.txt"

(cd "${WORKSPACE}" && west build --pristine -b "${BOARD}" "${APP}" \
    --build-dir build -- "-DEXTRA_DTC_OVERLAY_FILE=${DEMO_DIR}/novavisor.overlay")

mkdir -p "${CACHE}"
cp "${WORKSPACE}/build/zephyr/zephyr.bin" "${CACHE}/zephyr.bin"
cp "${WORKSPACE}/build/zephyr/zephyr.elf" "${CACHE}/zephyr.elf"
echo "${IMAGE_VERSION}" > "${STAMP}"
echo "==> Cached Zephyr ${IMAGE_VERSION} (${BOARD}) -> ${CACHE}"
