# NovaVisor DevContainer
#
# Built on Microsoft's multi-arch C++ devcontainer base (linux/amd64 + linux/arm64).
# The base image already ships build-essential, git, cmake (>= 3.25), python3,
# and a non-root `vscode` user with passwordless sudo — we only add the
# bare-metal hypervisor toolchain on top.
FROM mcr.microsoft.com/devcontainers/cpp:2-debian13

ARG USERNAME=vscode
ENV DEBIAN_FRONTEND=noninteractive \
    TOOLCHAIN_DIR=/opt/toolchain \
    CROSS_COMPILE=aarch64-none-elf-

# Bare-metal tooling on top of the base image.
#   ninja-build / clang-format / clang-tidy — build + lint (wired via task.sh)
#   qemu-system-arm / qemu-system-aarch64   — run & debug (task.sh run/debug)
#   gdb-multiarch                           — aarch64 remote debugging
#   device-tree-compiler                    — future DTB work (roadmap Phase 12)
#   shellcheck                              — task.sh / setup_env.sh lint
#   pipx                                    — isolates pre-commit under PEP 668
RUN apt-get update && apt-get install -y --no-install-recommends \
        ninja-build \
        qemu-system-arm \
        qemu-system-aarch64 \
        gdb-multiarch \
        clang-format \
        clang-tidy \
        device-tree-compiler \
        shellcheck \
        pipx \
    && rm -rf /var/lib/apt/lists/*

# pre-commit runs as the `vscode` user (git hook context); pipx keeps the
# install out of the system site-packages path.
RUN su - ${USERNAME} -c "pipx ensurepath && pipx install pre-commit"

# ARM GNU toolchain (aarch64-none-elf).
# versions.sh reads `uname -m` and picks the matching linux-x86_64 or
# linux-aarch64 tarball — so the same Dockerfile produces native toolchains
# whether the image is built on amd64 or arm64 hosts (no QEMU emulation).
COPY scripts/versions.sh /tmp/versions.sh
# bash (not sh/dash) — versions.sh uses ${VAR,,} parameter expansion.
RUN bash -c '. /tmp/versions.sh \
    && mkdir -p ${TOOLCHAIN_DIR} \
    && wget -q -O /tmp/toolchain.tar.xz "${TOOLCHAIN_URL}" \
    && tar -xf /tmp/toolchain.tar.xz -C ${TOOLCHAIN_DIR} \
    && ln -sfn ${TOOLCHAIN_DIR}/${TOOLCHAIN_EXTRACT_NAME} ${TOOLCHAIN_DIR}/current \
    && rm /tmp/toolchain.tar.xz /tmp/versions.sh'

ENV PATH=${TOOLCHAIN_DIR}/current/bin:${PATH}
