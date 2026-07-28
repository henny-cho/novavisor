#!/usr/bin/env bash
set -euo pipefail

WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "Usage: $0 <novavisor.bin> [output-directory]" >&2
    exit 2
fi

OUTPUT=()
if [[ $# -eq 2 ]]; then
    OUTPUT=(--output "$2")
fi
exec "${WORK_DIR}/scripts/nova" firmware package n1sdp \
    --payload "$1" "${OUTPUT[@]}"
