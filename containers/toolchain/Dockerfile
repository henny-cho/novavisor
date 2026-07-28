FROM ubuntu:26.04@sha256:3131b4cc82a783df6c9df078f86e01819a13594b865c2cad47bd1bca2b7063bb

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
