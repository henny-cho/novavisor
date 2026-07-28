#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ONLY=()
POSITIONAL=()
for argument in "$@"; do
    if [[ "${argument}" == "--build-only" ]]; then
        BUILD_ONLY=(--build-only)
    else
        POSITIONAL+=("${argument}")
    fi
done
if [[ ${#POSITIONAL[@]} -lt 1 || ${#POSITIONAL[@]} -gt 2 ]]; then
    echo "Usage: $0 <novavisor.bin> [output-dir] [--build-only]" >&2
    exit 2
fi

OUTPUT=()
if [[ ${#POSITIONAL[@]} -eq 2 ]]; then
    OUTPUT=(--output "${POSITIONAL[1]}")
fi
exec "${WORK_DIR}/scripts/nova" firmware verify qemu-tfa \
    --payload "${POSITIONAL[0]}" "${OUTPUT[@]}" "${BUILD_ONLY[@]}"
