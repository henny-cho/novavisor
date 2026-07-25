#!/usr/bin/env bash
# Boot the qemu_tfa BL33 image through a real Trusted Firmware-A chain
# (BL1 → BL2 → BL31 → BL33) on QEMU virt, and verify the firmware
# handoff contract without a physical board: EL2 entry, PSCI CPU_ON
# served by BL31 over SMC, and a guest reaching its exit marker.
#
# Usage: qemu_tfa_smoke.sh <novavisor.bin> [output-dir] [--build-only]
#   --build-only stops after packaging flash.bin (no QEMU run) — the
#   cheap variant CI can run without a TF-A-capable cache.
set -euo pipefail

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/versions.sh disable=SC1091
source "${WORK_DIR}/scripts/lib/versions.sh"
# shellcheck source=lib/tfa.sh disable=SC1091
source "${WORK_DIR}/scripts/lib/tfa.sh"
if [[ -d "${WORK_DIR}/.toolchain/current/bin" ]]; then
    export PATH="${WORK_DIR}/.toolchain/current/bin:${PATH}"
fi

BUILD_ONLY=0
POSITIONAL=()
for arg in "$@"; do
    case "${arg}" in
        --build-only) BUILD_ONLY=1 ;;
        *)            POSITIONAL+=("${arg}") ;;
    esac
done
if [[ ${#POSITIONAL[@]} -lt 1 || ${#POSITIONAL[@]} -gt 2 ]]; then
    echo "Usage: $0 <novavisor.bin> [output-dir] [--build-only]" >&2
    exit 2
fi
PAYLOAD="$(realpath "${POSITIONAL[0]}")"
OUTPUT_DIR="${POSITIONAL[1]:-${WORK_DIR}/build/qemu-tfa-firmware}"
mkdir -p "${OUTPUT_DIR}"
OUTPUT_DIR="$(cd "${OUTPUT_DIR}" && pwd -P)"

if [[ ! -f "${PAYLOAD}" ]]; then
    echo "Error: BL33 payload not found: ${PAYLOAD}" >&2
    exit 1
fi
if ! command -v aarch64-none-elf-gcc >/dev/null 2>&1; then
    echo "Error: aarch64-none-elf toolchain is not available" >&2
    exit 1
fi

tfa_prepare_source
BUILD_BASE="${OUTPUT_DIR}/tf-a-build"

# BL33 lives in the FIP and is loaded by BL2 at NS_IMAGE_OFFSET
# (0x60000000) — the address qemu_tfa's board_layout links against.
make -C "${TFA_SOURCE_DIR}" \
    BUILD_BASE="${BUILD_BASE}" \
    CROSS_COMPILE=aarch64-none-elf- \
    PLAT=qemu \
    QEMU_USE_GIC_DRIVER=QEMU_GICV3 \
    DEBUG=0 \
    BL33="${PAYLOAD}" \
    all fip

TFA_OUT="${BUILD_BASE}/qemu/release"
FLASH="${OUTPUT_DIR}/flash.bin"
# Secure flash layout expected by the qemu platform: BL1 at 0, FIP at
# 256 KiB (see TF-A docs/plat/qemu.rst).
rm -f "${FLASH}"
dd if="${TFA_OUT}/bl1.bin" of="${FLASH}" bs=4096 conv=notrunc status=none
dd if="${TFA_OUT}/fip.bin" of="${FLASH}" bs=4096 seek=64 conv=notrunc status=none
echo "QEMU TF-A flash image: ${FLASH}"

if [[ ${BUILD_ONLY} -eq 1 ]]; then
    exit 0
fi

# The canonical board model, plus the security state TF-A needs. No
# -kernel and no loader devices: everything boots from the flash chain.
# task.sh prints an environment banner first; the args are the last line.
read -r -a MACHINE_ARGS <<< "$("${WORK_DIR}/scripts/task.sh" demo qemu-args | tail -n 1)"
MACHINE_ARGS[1]="${MACHINE_ARGS[1]},secure=on"

LOG="${OUTPUT_DIR}/smoke.log"
: > "${LOG}"
qemu-system-aarch64 "${MACHINE_ARGS[@]}" -bios "${FLASH}" > "${LOG}" 2>&1 &
QEMU_PID=$!
trap 'kill "${QEMU_PID}" 2>/dev/null || true' EXIT

# Ordered contract markers: the chain reached BL31, BL33 entered at EL2
# and came up, BL31 served PSCI CPU_ON for the secondary, the embedded
# guest ran and exited.
MARKERS=(
    "BL31: v"
    "NovaVisor booted"
    "core 1 online"
    "Hello from EL1 guest"
    "demo_exit code=0"
)
DEADLINE=$((SECONDS + 120))
NEXT=0
while ((SECONDS < DEADLINE && NEXT < ${#MARKERS[@]})); do
    while ((NEXT < ${#MARKERS[@]})) && grep -qF "${MARKERS[${NEXT}]}" "${LOG}"; do
        echo "[qemu-tfa-smoke] matched[$((NEXT + 1))/${#MARKERS[@]}] ${MARKERS[${NEXT}]}"
        NEXT=$((NEXT + 1))
    done
    if ! kill -0 "${QEMU_PID}" 2>/dev/null; then
        break
    fi
    sleep 1
done

if ((NEXT < ${#MARKERS[@]})); then
    echo "[qemu-tfa-smoke] FAIL: missing '${MARKERS[${NEXT}]}' (log tail below)" >&2
    tail -40 "${LOG}" >&2
    exit 1
fi
echo "[qemu-tfa-smoke] PASS: TF-A chain handoff contract verified"
