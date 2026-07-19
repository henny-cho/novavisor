#!/usr/bin/env bash
# Assemble the mixed-criticality guest pair from the Zephyr (12) and
# Linux (13) image pipelines — no build of its own. Idempotent: the
# stamp combines both upstream stamps, so a rebuilt upstream image
# re-syncs this cache on the next fetch.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GUESTS="${REPO}/external/cache/guests"
CACHE="${GUESTS}/14_mixed_recovery"
STAMP="${CACHE}/mixed.version"

bash "${REPO}/demo/12_zephyr/fetch.sh"
bash "${REPO}/demo/13_linux/fetch.sh"

VERSION="$(cat "${GUESTS}/12_zephyr/zephyr.version")+$(cat "${GUESTS}/13_linux/linux.version")"
if [[ -f "${STAMP}" && "$(cat "${STAMP}")" == "${VERSION}" ]]; then
    echo "==> Mixed pair already cached (${CACHE})"
    exit 0
fi

mkdir -p "${CACHE}"
cp "${GUESTS}/12_zephyr/zephyr.bin" "${GUESTS}/12_zephyr/zephyr.elf" \
   "${GUESTS}/13_linux/Image" "${GUESTS}/13_linux/Image.elf" "${CACHE}/"
echo "${VERSION}" > "${STAMP}"
echo "==> Cached mixed pair -> ${CACHE}"
