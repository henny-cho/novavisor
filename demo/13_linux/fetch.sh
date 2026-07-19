#!/usr/bin/env bash
# Build the pinned Linux kernel (trimmed arm64 defconfig) with an
# embedded static-BusyBox initramfs and populate
# external/cache/guests/13_linux/. Idempotent: a cache hit with a
# matching version stamp performs zero network access and no build.
#
# The whole recipe lives in this one file (config fragment, initramfs
# list, /init) so the CI guest-image cache key — a hash of fetch.sh —
# invalidates exactly when the produced image would change.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

LINUX_VER="6.12.30"
LINUX_URL="https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-${LINUX_VER}.tar.xz"
BUSYBOX_VER="1.36.1"
BUSYBOX_URL="https://busybox.net/downloads/busybox-${BUSYBOX_VER}.tar.bz2"
RECIPE_REV="r3" # bump when the recipe below changes without a version bump

WORKSPACE="${REPO}/external/linux"
CACHE="${REPO}/external/cache/guests/13_linux"
STAMP="${CACHE}/linux.version"
VERSION="linux-${LINUX_VER}+busybox-${BUSYBOX_VER}+${RECIPE_REV}"

if [[ -f "${STAMP}" && "$(cat "${STAMP}")" == "${VERSION}" &&
      -f "${CACHE}/Image" && -f "${CACHE}/Image.elf" ]]; then
    echo "==> Linux ${VERSION} already cached (${CACHE})"
    exit 0
fi

# Kernel and BusyBox target Linux/EL1 userspace — the hypervisor's
# bare-metal GCC has no libc, so use the distro aarch64-linux-gnu
# toolchain (the native compiler under an alias on an arm64 host).
CROSS=aarch64-linux-gnu-
command -v "${CROSS}gcc" >/dev/null || {
    echo "fetch: ${CROSS}gcc not found (apt install gcc-aarch64-linux-gnu)" >&2
    exit 1
}

mkdir -p "${WORKSPACE}"
cd "${WORKSPACE}"

# --- BusyBox: one static binary, the entire userspace ----------------
if [[ ! -x "busybox-${BUSYBOX_VER}/busybox" ]]; then
    [[ -f "busybox-${BUSYBOX_VER}.tar.bz2" ]] ||
        curl -fL -o "busybox-${BUSYBOX_VER}.tar.bz2" "${BUSYBOX_URL}"
    tar xf "busybox-${BUSYBOX_VER}.tar.bz2"
    (
        cd "busybox-${BUSYBOX_VER}"
        make defconfig
        sed -i -e 's/^# CONFIG_STATIC is not set/CONFIG_STATIC=y/' \
            -e 's/^CONFIG_TC=y/# CONFIG_TC is not set/' .config # tc uses a kernel API removed in 6.8
        make -j"$(nproc)" CROSS_COMPILE="${CROSS}" busybox
    )
fi

# --- initramfs: /init mounts proc/sys, proves userspace, drops to sh.
# A `nova_mixed` kernel command line switches it into the
# mixed-criticality scenario driver: the IVC page (0x60000000, mapped
# into every VM and untouched by a warm reset) holds a boot counter,
# so the first boot generates CPU load and panics itself (panic=-1
# warm-resets the VM from pristine) while the second boot lands on
# the shell. One image serves both the plain and the mixed demo.
cat > init.sh <<'EOF'
#!/bin/sh
/bin/busybox mount -t proc proc /proc
/bin/busybox mount -t sysfs sysfs /sys
/bin/busybox --install -s /bin
uname -a
if grep -q nova_mixed /proc/cmdline; then
    boot=$(($(devmem 0x60000F00 32) + 1))
    devmem 0x60000F00 32 $boot
    echo "NOVA MIXED BOOT $boot"
    if [ "$boot" = 1 ]; then
        echo "NOVA LOAD START"
        md5sum /dev/zero &
        md5sum /dev/zero &
        sleep 20
        echo "NOVA LOAD DONE"
        echo c > /proc/sysrq-trigger
    fi
fi
exec /bin/sh
EOF

cat > initramfs.list <<EOF
dir /dev 0755 0 0
nod /dev/console 0600 0 0 c 5 1
nod /dev/mem 0600 0 0 c 1 1
nod /dev/zero 0666 0 0 c 1 5
nod /dev/null 0666 0 0 c 1 3
dir /proc 0755 0 0
dir /sys 0755 0 0
dir /bin 0755 0 0
dir /tmp 1777 0 0
file /bin/busybox ${WORKSPACE}/busybox-${BUSYBOX_VER}/busybox 0755 0 0
slink /bin/sh busybox 0777 0 0
file /init ${WORKSPACE}/init.sh 0755 0 0
EOF

# --- Kernel: defconfig minus everything QEMU virt does not have ------
if [[ ! -d "linux-${LINUX_VER}" ]]; then
    [[ -f "linux-${LINUX_VER}.tar.xz" ]] ||
        curl -fL -o "linux-${LINUX_VER}.tar.xz" "${LINUX_URL}"
    tar xf "linux-${LINUX_VER}.tar.xz"
fi
cd "linux-${LINUX_VER}"
make ARCH=arm64 CROSS_COMPILE="${CROSS}" defconfig
# Every vendor platform select pulls clk/pinctrl/soc driver stacks that
# dominate build time; QEMU virt needs none of them (PL011, GICv3 and
# the arch timer are ARM-generic). Invisible ARCH_* infra symbols the
# tool also touches are re-selected by olddefconfig.
grep -oP '^CONFIG_ARCH_[A-Z0-9_]+(?==y)' .config | while read -r sym; do
    scripts/config --disable "${sym#CONFIG_}"
done
scripts/config \
    --disable MODULES --disable NET --disable PCI --disable ACPI \
    --disable EFI --disable VIRTUALIZATION --disable CPU_FREQ \
    --disable SCSI --disable ATA --disable MD --disable MTD \
    --disable USB_SUPPORT --disable MMC --disable I2C --disable SPI \
    --disable SOUND --disable DRM --disable FB --disable MEDIA_SUPPORT \
    --disable REGULATOR --disable BTRFS_FS --disable SERIAL_8250 \
    --disable IPMI_HANDLER --disable VFIO --disable CORESIGHT \
    --disable EDAC --disable GNSS --enable DEBUG_INFO_NONE \
    --set-str INITRAMFS_SOURCE "${WORKSPACE}/initramfs.list"
make ARCH=arm64 CROSS_COMPILE="${CROSS}" olddefconfig
make ARCH=arm64 CROSS_COMPILE="${CROSS}" -j"$(nproc)" Image vmlinux

mkdir -p "${CACHE}"
cp arch/arm64/boot/Image "${CACHE}/Image"
cp vmlinux "${CACHE}/Image.elf" # gdb-symbol fallback name the demo runner expects
echo "${VERSION}" > "${STAMP}"
echo "==> Cached ${VERSION} -> ${CACHE}"
