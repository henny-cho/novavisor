#!/usr/bin/env bash
# Pinned Trusted Firmware-A source checkout, shared by the per-board
# firmware scripts (n1sdp_firmware.sh, qemu_tfa_smoke.sh). Requires
# WORK_DIR and the TFA_* pins from lib/versions.sh; sets TFA_SOURCE_DIR.

tfa_prepare_source() {
    TFA_SOURCE_DIR="${WORK_DIR}/external/cache/firmware/arm-trusted-firmware-${TFA_VERSION}"
    mkdir -p "$(dirname "${TFA_SOURCE_DIR}")"
    if [[ ! -d "${TFA_SOURCE_DIR}/.git" ]]; then
        git clone --filter=blob:none --no-checkout \
            https://github.com/ARM-software/arm-trusted-firmware.git "${TFA_SOURCE_DIR}"
    fi
    git -C "${TFA_SOURCE_DIR}" fetch --depth=1 origin "${TFA_COMMIT}"
    git -C "${TFA_SOURCE_DIR}" checkout --detach "${TFA_COMMIT}"
    if [[ "$(git -C "${TFA_SOURCE_DIR}" rev-parse HEAD)" != "${TFA_COMMIT}" ]]; then
        echo "Error: Trusted Firmware-A revision verification failed" >&2
        exit 1
    fi
}
