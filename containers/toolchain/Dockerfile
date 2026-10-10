FROM ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7

ARG DEBIAN_FRONTEND=noninteractive
ENV NOVA_TOOLCHAIN_DIR=/opt/toolchain \
    CROSS_COMPILE=aarch64-none-elf- \
    PATH=/opt/toolchain/current/bin:/usr/local/bin:${PATH}

COPY bootstrap /opt/novavisor/
COPY novakit/python-env \
     novakit/requirements-cli.txt \
     novakit/tool-versions.env \
     /opt/novavisor/novakit/
RUN /opt/novavisor/bootstrap --image \
    && useradd --create-home --shell /bin/bash nova \
    && echo "nova ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/nova \
    && chmod 0440 /etc/sudoers.d/nova

WORKDIR /workspace
