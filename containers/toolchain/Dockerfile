FROM ubuntu:26.04

ARG DEBIAN_FRONTEND=noninteractive
ENV NOVA_TOOLCHAIN_DIR=/opt/toolchain \
    CROSS_COMPILE=aarch64-none-elf- \
    PATH=/opt/toolchain/current/bin:/usr/local/bin:${PATH}

COPY scripts/bootstrap scripts/tool-versions.env /opt/novavisor/scripts/
RUN /opt/novavisor/scripts/bootstrap --image \
    && useradd --create-home --shell /bin/bash nova \
    && echo "nova ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/nova \
    && chmod 0440 /etc/sudoers.d/nova

WORKDIR /workspace
