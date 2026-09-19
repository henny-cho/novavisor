FROM ubuntu:26.04@sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b

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
