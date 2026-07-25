#!/usr/bin/env bash
# Resolve aarch64-none-elf-gdb across NovaVisor environments:
#   1. Project-local toolchain (.toolchain/current, populated by setup_env.sh).
#   2. System PATH (devcontainer image or user-installed).
# Used by .vscode/launch.json and cmake.debugConfig as miDebuggerPath so the
# VS Code Debug button works under both setup methods from the README.
set -euo pipefail
WORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL="${WORK_DIR}/.toolchain/current/bin/aarch64-none-elf-gdb"
if [ -x "${LOCAL}" ]; then
    exec "${LOCAL}" "$@"
fi
if command -v aarch64-none-elf-gdb >/dev/null 2>&1; then
    exec aarch64-none-elf-gdb "$@"
fi
echo "aarch64-none-elf-gdb not found." >&2
echo "  - Devcontainer users: reopen in container (.devcontainer/)." >&2
echo "  - Manual setup: run ./scripts/setup_env.sh." >&2
exit 127
