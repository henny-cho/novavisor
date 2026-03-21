FROM ubuntu:24.04

# Avoid prompts from apt
ENV DEBIAN_FRONTEND=noninteractive

# Install base dependencies for building & simulation
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    ninja-build \
    git \
    wget \
    xz-utils \
    qemu-system-arm \
    python3 \
    python3-pip \
    device-tree-compiler \
    clang-format \
    clang-tidy \
    gdb-multiarch \
    pre-commit \
    && rm -rf /var/lib/apt/lists/*

# Set up workspace
WORKDIR /workspace

# ARM GNU Toolchain configuration
COPY scripts/versions.sh /tmp/versions.sh
ENV TOOLCHAIN_DIR="/opt/toolchain"

# Download and extract the bare-metal toolchain
RUN . /tmp/versions.sh && \
    mkdir -p ${TOOLCHAIN_DIR} && \
    wget -q --show-progress -c ${TOOLCHAIN_URL} -O ${TOOLCHAIN_DIR}/${TOOLCHAIN_TAR} && \
    tar -xf ${TOOLCHAIN_DIR}/${TOOLCHAIN_TAR} -C ${TOOLCHAIN_DIR} && \
    ln -sfn ${TOOLCHAIN_DIR}/${TOOLCHAIN_EXTRACT_NAME} ${TOOLCHAIN_DIR}/current && \
    rm -f ${TOOLCHAIN_DIR}/${TOOLCHAIN_TAR}

# Add toolchain to PATH
ENV PATH="${TOOLCHAIN_DIR}/current/bin:${PATH}"
ENV CROSS_COMPILE="aarch64-none-elf-"

# Create a non-root user for development
ARG USERNAME=dev
ARG USER_UID=1000
ARG USER_GID=$USER_UID
RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m $USERNAME \
    && apt-get update \
    && apt-get install -y sudo \
    && echo $USERNAME ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME

USER $USERNAME
