#!/usr/bin/env python3
"""YAML guest/device config -> FDT blobs, runtime policy, and assembly.

Reads a guest configuration YAML (configs/*.yml), validates it against
the platform layout and selected board inventory, and emits:

  <outdir>/guest<N>.dtb   one FDT (v17) per guest, the blob the guest
                          receives in x0 and the hypervisor parses to
                          build its runtime guest table
  <outdir>/guest_dtbs.S   .incbin wrapper exposing g_guest_dtbs[] /
                          g_guest_dtb_count to the hypervisor image
  <outdir>/device_policy.hpp
                          static DMA ownership and device tables

Pure stdlib + PyYAML — no dtc dependency, so CI needs nothing new.
Inspect a blob locally with: dtc -I dtb -O dts <outdir>/guest0.dtb
"""

import argparse
import hashlib
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
DEFAULT_LAYOUT = REPO / "src" / "nova" / "abi" / "guest_layout.h"
GIC_REGS = REPO / "src" / "nova" / "arch" / "gicv3_regs.h"

# QEMU virt RAM ceiling (-m 1024 in task.sh run / demo_runner.py):
# 0x4000_0000 + 1 GiB. Pristine copies must stay below it.
RAM_END = 0x8000_0000

MIB = 0x10_0000

# ---------------------------------------------------------------------------
# Layout header
# ---------------------------------------------------------------------------

def read_defines(path: Path, wanted: list[str]) -> dict[str, int]:
    """Pull #define constants from a platform header (single source)."""
    text = path.read_text()
    values: dict[str, int] = {}
    for name in wanted:
        m = re.search(rf"#define\s+{name}\s+(0[xX][0-9a-fA-F]+|\d+)", text)
        if not m:
            sys.exit(f"yml2dtb: {name} not found in {path}")
        values[name] = int(m.group(1), 0)
    return values


def read_layout(path: Path, board_layout: Path) -> dict[str, int]:
    values = read_defines(path, [
        "NOVA_GUEST_IPA_BASE", "NOVA_GUEST_IPA_SIZE", "NOVA_GUEST_PA_ALIGN",
        "NOVA_IVC_SHM_IPA", "NOVA_IVC_SHM_PA", "NOVA_IVC_SHM_SIZE",
        "NOVA_GUEST_PRISTINE_PA", "NOVA_GUEST_DTB_SIZE",
        "NOVA_VUART_IPA_BASE", "NOVA_VUART_IPA_SIZE", "NOVA_VUART_SPI",
        "NOVA_GICD_IPA_BASE", "NOVA_GICR_IPA_BASE",
    ])
    values |= read_defines(GIC_REGS, ["NOVA_GICD_FRAME_SIZE", "NOVA_GICR_FRAME_SIZE"])
    values |= read_defines(board_layout, [
        "NOVA_BOARD_SMP_CPUS", "NOVA_BOARD_RAM_BASE", "NOVA_BOARD_RAM_SIZE",
        "NOVA_BOARD_UART0_BASE", "NOVA_BOARD_UART0_INTID",
        "NOVA_BOARD_GICD_BASE", "NOVA_BOARD_GICR_BASE",
        "NOVA_BOARD_SMMU_BASE", "NOVA_BOARD_SMMU_SIZE",
        "NOVA_BOARD_SMMU_EVENT_INTID", "NOVA_BOARD_SMMU_CMD_INTID",
        "NOVA_BOARD_SMMU_ERROR_INTID",
    ])
    return values


# ---------------------------------------------------------------------------
# FDT (v17) writer
# ---------------------------------------------------------------------------

FDT_MAGIC      = 0xD00D_FEED
FDT_BEGIN_NODE = 0x1
FDT_END_NODE   = 0x2
FDT_PROP       = 0x3
FDT_END        = 0x9


class FdtWriter:
    """Minimal flattened-device-tree serializer (spec v0.4, dt version 17)."""

    def __init__(self) -> None:
        self.struct = bytearray()
        self.strings = bytearray()
        self.str_off: dict[str, int] = {}
        self.depth = 0

    def _pad4(self) -> None:
        while len(self.struct) % 4:
            self.struct.append(0)

    def _string_offset(self, name: str) -> int:
        if name not in self.str_off:
            self.str_off[name] = len(self.strings)
            self.strings += name.encode() + b"\0"
        return self.str_off[name]

    def begin_node(self, name: str) -> None:
        self.struct += struct.pack(">I", FDT_BEGIN_NODE)
        self.struct += name.encode() + b"\0"
        self._pad4()
        self.depth += 1

    def end_node(self) -> None:
        self.struct += struct.pack(">I", FDT_END_NODE)
        self.depth -= 1

    def prop(self, name: str, data: bytes = b"") -> None:
        self.struct += struct.pack(">III", FDT_PROP, len(data), self._string_offset(name))
        self.struct += data
        self._pad4()

    def prop_u32(self, name: str, *values: int) -> None:
        self.prop(name, b"".join(struct.pack(">I", v) for v in values))

    def prop_u64(self, name: str, *values: int) -> None:
        self.prop(name, b"".join(struct.pack(">Q", v) for v in values))

    def prop_str(self, name: str, *values: str) -> None:
        self.prop(name, b"".join(v.encode() + b"\0" for v in values))

    def finish(self) -> bytes:
        assert self.depth == 0, "unbalanced begin/end_node"
        self.struct += struct.pack(">I", FDT_END)
        header_size = 40
        rsvmap = struct.pack(">QQ", 0, 0)  # single terminating entry
        off_rsvmap = header_size
        off_struct = off_rsvmap + len(rsvmap)
        off_strings = off_struct + len(self.struct)
        total = off_strings + len(self.strings)
        header = struct.pack(
            ">10I", FDT_MAGIC, total, off_struct, off_strings, off_rsvmap,
            17, 16, 0, len(self.strings), len(self.struct))
        return header + rsvmap + bytes(self.struct) + bytes(self.strings)


# Fixed phandles for the two referenced nodes (a full allocator is
# overkill for a tree this small).
PHANDLE_GIC = 1
PHANDLE_CLK = 2

# GIC interrupt specifier: <type intid-offset flags>.
GIC_SPI, GIC_PPI, EDGE_RISING, LEVEL_HIGH = 0, 1, 1, 4


def build_guest_dtb(guest: dict, layout: dict[str, int]) -> bytes:
    ipa_base = layout["NOVA_GUEST_IPA_BASE"]
    w = FdtWriter()
    w.begin_node("")
    w.prop_str("compatible", "novavisor,guest")
    w.prop_str("model", guest["name"])
    w.prop_u32("#address-cells", 2)
    w.prop_u32("#size-cells", 2)
    w.prop_u32("interrupt-parent", PHANDLE_GIC)
    # Hypervisor-only placement hints (vendor-prefixed; guests ignore
    # them): boot without a guest-issued VM_START, per-vCPU core pin.
    if guest["autostart"]:
        w.prop("nova,autostart")
    if guest["cores"] is not None:
        w.prop_u32("nova,affinity", *guest["cores"])

    w.begin_node(f"memory@{ipa_base:x}")
    w.prop_str("device_type", "memory")
    w.prop_u64("reg", ipa_base, guest["memory_size"])
    w.end_node()

    w.begin_node("cpus")
    w.prop_u32("#address-cells", 1)
    w.prop_u32("#size-cells", 0)
    for n in range(guest["vcpus"]):
        w.begin_node(f"cpu@{n}")
        w.prop_str("device_type", "cpu")
        w.prop_str("compatible", "arm,cortex-a57")
        w.prop_u32("reg", n)
        w.prop_str("enable-method", "psci")
        w.end_node()
    w.end_node()

    w.begin_node("psci")
    w.prop_str("compatible", "arm,psci-1.0", "arm,psci-0.2")
    w.prop_str("method", "hvc")
    w.end_node()

    # The emulated GICv3: one distributor frame plus one redistributor
    # frame per vCPU (sizes single-sourced from gicv3_regs.h).
    gicd = layout["NOVA_GICD_IPA_BASE"]
    w.begin_node(f"intc@{gicd:x}")
    w.prop_str("compatible", "arm,gic-v3")
    w.prop("interrupt-controller")
    w.prop_u32("#interrupt-cells", 3)
    w.prop_u64("reg", gicd, layout["NOVA_GICD_FRAME_SIZE"],
               layout["NOVA_GICR_IPA_BASE"],
               layout["NOVA_GICR_FRAME_SIZE"] * guest["vcpus"])
    w.prop_u32("phandle", PHANDLE_GIC)
    w.end_node()

    # Architected timer PPIs: sec-phys 13, phys 14, virt 11, hyp 10.
    # Guests use the virtual timer; the others trap (CNTP is RAZ/WI).
    w.begin_node("timer")
    w.prop_str("compatible", "arm,armv8-timer")
    w.prop_u32("interrupts",
               GIC_PPI, 13, LEVEL_HIGH, GIC_PPI, 14, LEVEL_HIGH,
               GIC_PPI, 11, LEVEL_HIGH, GIC_PPI, 10, LEVEL_HIGH)
    w.end_node()

    if guest["uart"] == "vuart":
        # PL011 drivers (Linux amba-pl011) require a baud clock; the
        # vuart ignores baud programming, so any fixed rate works.
        w.begin_node("apb-pclk")
        w.prop_str("compatible", "fixed-clock")
        w.prop_u32("#clock-cells", 0)
        w.prop_u32("clock-frequency", 24_000_000)
        w.prop_str("clock-output-names", "clk24mhz")
        w.prop_u32("phandle", PHANDLE_CLK)
        w.end_node()

        uart_base = layout["NOVA_VUART_IPA_BASE"]
        w.begin_node(f"uart@{uart_base:x}")
        w.prop_str("compatible", "arm,pl011", "arm,primecell")
        w.prop_u64("reg", uart_base, 0x1000)
        w.prop_u32("interrupts", GIC_SPI, layout["NOVA_VUART_SPI"] - 32, LEVEL_HIGH)
        w.prop_u32("clocks", PHANDLE_CLK, PHANDLE_CLK)
        w.prop_str("clock-names", "uartclk", "apb_pclk")
        w.end_node()

    for device in guest["devices"]:
        guest_ipa = device["guest_ipa"]
        w.begin_node(f"{device['id']}@{guest_ipa:x}")
        w.prop_str("compatible", device["compatible"])
        w.prop_u64("reg", guest_ipa, device["mmio_size"])
        irq_flags = LEVEL_HIGH if device["trigger"] == "level" else EDGE_RISING
        w.prop_u32("interrupts", GIC_SPI, device["virtual_intid"] - 32, irq_flags)
        w.prop_u64("nova,dma-window", ipa_base, guest["memory_size"])
        if device["coherent"]:
            w.prop("dma-coherent")
        w.end_node()

    w.begin_node("chosen")
    if guest["uart"] == "vuart":
        w.prop_str("stdout-path", f"/uart@{layout['NOVA_VUART_IPA_BASE']:x}")
    if guest["bootargs"]:
        w.prop_str("bootargs", guest["bootargs"])
    w.end_node()

    w.end_node()
    return w.finish()


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def config_error(path: Path, message: str) -> None:
    sys.exit(f"yml2dtb: {path}: {message}")


def integer(value: object, path: Path, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        config_error(path, f"{field} must be an integer")
    return value


def range_valid(base: int, size: int) -> bool:
    return base >= 0 and size > 0 and base <= (1 << 64) - size


def ranges_overlap(lhs_base: int, lhs_size: int, rhs_base: int, rhs_size: int) -> bool:
    return lhs_base < rhs_base + rhs_size and rhs_base < lhs_base + lhs_size


def load_inventory(path: Path, layout: dict[str, int]) -> dict:
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict):
        config_error(path, "inventory must be a mapping")
    sid_bits = integer(doc.get("sid_bits"), path, "sid_bits")
    if not 1 <= sid_bits <= 32:
        config_error(path, "sid_bits must be in [1, 32]")
    records = doc.get("devices")
    if not isinstance(records, list):
        config_error(path, "'devices' must be a list")

    protected = [
        ("EL2 RAM", layout["NOVA_BOARD_RAM_BASE"], layout["NOVA_BOARD_RAM_SIZE"]),
        ("UART", layout["NOVA_BOARD_UART0_BASE"], 0x1000),
        ("GICD", layout["NOVA_BOARD_GICD_BASE"], layout["NOVA_GICD_FRAME_SIZE"]),
        ("GICR", layout["NOVA_BOARD_GICR_BASE"],
         layout["NOVA_GICR_FRAME_SIZE"] * layout["NOVA_BOARD_SMP_CPUS"]),
        ("SMMU", layout["NOVA_BOARD_SMMU_BASE"], layout["NOVA_BOARD_SMMU_SIZE"]),
    ]
    parsed: list[dict] = []
    names: set[str] = set()
    numeric_ids: set[int] = set()
    streams: set[int] = set()
    interrupts: set[int] = {
        layout["NOVA_BOARD_UART0_INTID"],
        layout["NOVA_BOARD_SMMU_EVENT_INTID"],
        layout["NOVA_BOARD_SMMU_CMD_INTID"],
        layout["NOVA_BOARD_SMMU_ERROR_INTID"],
    }
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            config_error(path, f"devices[{index}] must be a mapping")
        name = record.get("id")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
            config_error(path, f"devices[{index}].id must be a logical identifier")
        if name in names:
            config_error(path, f"duplicate device id '{name}'")
        names.add(name)

        device_id = integer(record.get("device_id"), path, f"{name}.device_id")
        if not 0 <= device_id < 0xFFFF or device_id in numeric_ids:
            config_error(path, f"{name}.device_id is invalid or duplicated")
        numeric_ids.add(device_id)
        compatible = record.get("compatible")
        if not isinstance(compatible, str) or not compatible:
            config_error(path, f"{name}.compatible must be a non-empty string")

        device_streams = record.get("streams")
        if not isinstance(device_streams, list) or not device_streams:
            config_error(path, f"{name}.streams must contain at least one SID")
        parsed_streams = []
        for stream in device_streams:
            sid = integer(stream, path, f"{name}.streams")
            if not 0 <= sid < (1 << sid_bits) or sid in streams:
                config_error(path, f"{name} has an out-of-range or duplicate SID {sid}")
            streams.add(sid)
            parsed_streams.append(sid)

        mmio = record.get("mmio")
        if not isinstance(mmio, dict):
            config_error(path, f"{name}.mmio must be a mapping")
        mmio_base = integer(mmio.get("base"), path, f"{name}.mmio.base")
        mmio_size = integer(mmio.get("size"), path, f"{name}.mmio.size")
        if not range_valid(mmio_base, mmio_size) or mmio_base % 0x1000 or mmio_size % 0x1000:
            config_error(path, f"{name}.mmio must be a page-aligned non-empty range")
        for protected_name, base, size in protected:
            if ranges_overlap(mmio_base, mmio_size, base, size):
                config_error(path, f"{name}.mmio overlaps protected {protected_name}")
        for device in parsed:
            if ranges_overlap(mmio_base, mmio_size, device["mmio_base"], device["mmio_size"]):
                config_error(path, f"{name}.mmio overlaps {device['id']}.mmio")

        irq = record.get("interrupt")
        if not isinstance(irq, dict):
            config_error(path, f"{name}.interrupt must be a mapping")
        intid = integer(irq.get("intid"), path, f"{name}.interrupt.intid")
        trigger = irq.get("trigger")
        if not 32 <= intid < 1020 or intid in interrupts:
            config_error(path, f"{name}.interrupt.intid is invalid or duplicated")
        if trigger not in ("level", "edge"):
            config_error(path, f"{name}.interrupt.trigger must be level or edge")
        interrupts.add(intid)

        coherent = record.get("coherent", False)
        if not isinstance(coherent, bool):
            config_error(path, f"{name}.coherent must be boolean")
        reset = record.get("reset")
        if reset not in ("none", "quiesce", "function"):
            config_error(path, f"{name}.reset must be none, quiesce, or function")
        parsed.append({
            "id": name, "device_id": device_id, "compatible": compatible,
            "streams": parsed_streams, "mmio_base": mmio_base,
            "mmio_size": mmio_size, "physical_intid": intid,
            "trigger": trigger, "coherent": coherent, "reset": reset,
        })

    return {"sid_bits": sid_bits, "devices": parsed,
            "by_id": {device["id"]: device for device in parsed}}


def load_config(path: Path, layout: dict[str, int], inventory: dict) -> tuple[list[dict], list[dict]]:
    doc = yaml.safe_load(path.read_text())
    guests = doc.get("guests") if isinstance(doc, dict) else None
    if not isinstance(guests, list) or not 1 <= len(guests) <= 4:  # kMaxGuests
        sys.exit(f"yml2dtb: {path}: 'guests' must list 1..4 entries")

    # PA windows pack from the IPA base with Block-aligned starts —
    # the same cursor rule guest_config.cpp applies at boot. The whole
    # packed region must stay below the IVC page.
    align = layout["NOVA_GUEST_PA_ALIGN"]
    load_pa = layout["NOVA_GUEST_IPA_BASE"]
    parsed = []
    for i, g in enumerate(guests):
        name = g.get("name", f"vm{i}")
        size = int(g.get("memory_size", layout["NOVA_GUEST_IPA_SIZE"]))
        vcpus = int(g.get("vcpus", 1))
        uart = g.get("uart", "none")
        bootargs = g.get("bootargs", "")
        cores = g.get("cores")
        autostart = bool(g.get("autostart", False))
        if not 1 <= vcpus <= 2:  # kMaxVcpusPerVm
            sys.exit(f"yml2dtb: {name}: vcpus {vcpus} (supported: 1..2)")
        if uart not in ("none", "vuart"):
            sys.exit(f"yml2dtb: {name}: uart '{uart}' (supported: none, vuart)")
        if not isinstance(bootargs, str):
            sys.exit(f"yml2dtb: {name}: bootargs must be a string")
        if cores is not None:
            smp = layout["NOVA_BOARD_SMP_CPUS"]
            if (not isinstance(cores, list) or len(cores) != vcpus or
                    not all(isinstance(c, int) and 0 <= c < smp for c in cores)):
                sys.exit(f"yml2dtb: {name}: cores must list one core index "
                         f"in [0, {smp}) per vcpu ({vcpus})")
        if size < layout["NOVA_GUEST_IPA_SIZE"] or size % MIB:
            sys.exit(f"yml2dtb: {name}: memory_size {size:#x} must be a MiB "
                     f"multiple >= {layout['NOVA_GUEST_IPA_SIZE']:#x} (linker window)")
        if load_pa + size > layout["NOVA_IVC_SHM_PA"]:
            sys.exit(f"yml2dtb: {name}: window {load_pa:#x}+{size:#x} overlaps "
                     f"the IVC page at {layout['NOVA_IVC_SHM_PA']:#x}")
        parsed.append({"name": name, "memory_size": size, "vcpus": vcpus,
                       "uart": uart, "bootargs": bootargs, "cores": cores,
                       "autostart": autostart, "load_pa": load_pa, "devices": []})
        load_pa = (load_pa + size + align - 1) & ~(align - 1)

    pristine_end = layout["NOVA_GUEST_PRISTINE_PA"] + sum(g["memory_size"] for g in parsed)
    if pristine_end > RAM_END:
        sys.exit(f"yml2dtb: pristine copies end at {pristine_end:#x}, past RAM end {RAM_END:#x}")

    physical_ranges = [
        (guest["name"], guest["load_pa"], guest["memory_size"]) for guest in parsed
    ] + [
        ("IVC", layout["NOVA_IVC_SHM_PA"], layout["NOVA_IVC_SHM_SIZE"]),
        ("pristine", layout["NOVA_GUEST_PRISTINE_PA"],
         sum(guest["memory_size"] for guest in parsed)),
    ]
    for device in inventory["devices"]:
        for name, base, size in physical_ranges:
            if ranges_overlap(device["mmio_base"], device["mmio_size"], base, size):
                config_error(path, f"{device['id']}.mmio overlaps {name} physical memory")

    project_devices = doc.get("devices", [])
    if not isinstance(project_devices, list):
        config_error(path, "'devices' must be a list")
    assigned: list[dict] = []
    assigned_ids: set[str] = set()
    guest_regions: list[tuple[int, int, int, str]] = []
    guest_interrupts: set[tuple[int, int]] = set()
    for index, assignment in enumerate(project_devices):
        if not isinstance(assignment, dict):
            config_error(path, f"devices[{index}] must be a mapping")
        name = assignment.get("id")
        if not isinstance(name, str) or name not in inventory["by_id"]:
            config_error(path, f"devices[{index}].id is not in the board inventory")
        if name in assigned_ids:
            config_error(path, f"duplicate device assignment '{name}'")
        assigned_ids.add(name)
        owner = integer(assignment.get("owner"), path, f"{name}.owner")
        if not 0 <= owner < len(parsed):
            config_error(path, f"{name}.owner {owner} is outside the guest table")
        guest_ipa = integer(assignment.get("guest_ipa"), path, f"{name}.guest_ipa")
        virtual_intid = integer(assignment.get("virtual_intid"), path, f"{name}.virtual_intid")
        device = inventory["by_id"][name]
        if not range_valid(guest_ipa, device["mmio_size"]) or guest_ipa % 0x1000:
            config_error(path, f"{name}.guest_ipa must be a page-aligned valid range")
        if not 32 <= virtual_intid < 1020:
            config_error(path, f"{name}.virtual_intid must be an SPI")

        reserved_ipa = [
            ("guest RAM", layout["NOVA_GUEST_IPA_BASE"], parsed[owner]["memory_size"]),
            ("IVC", layout["NOVA_IVC_SHM_IPA"], layout["NOVA_IVC_SHM_SIZE"]),
            ("GICD", layout["NOVA_GICD_IPA_BASE"], layout["NOVA_GICD_FRAME_SIZE"]),
            ("GICR", layout["NOVA_GICR_IPA_BASE"],
             layout["NOVA_GICR_FRAME_SIZE"] * parsed[owner]["vcpus"]),
            ("vuart", layout["NOVA_VUART_IPA_BASE"], layout["NOVA_VUART_IPA_SIZE"]),
        ]
        for reserved_name, base, size in reserved_ipa:
            if ranges_overlap(guest_ipa, device["mmio_size"], base, size):
                config_error(path, f"{name}.guest_ipa overlaps {reserved_name}")
        for other_owner, base, size, other_name in guest_regions:
            if owner == other_owner and ranges_overlap(guest_ipa, device["mmio_size"], base, size):
                config_error(path, f"{name}.guest_ipa overlaps {other_name}.guest_ipa")
        if (owner, virtual_intid) in guest_interrupts:
            config_error(path, f"{name}.virtual_intid duplicates another owner IRQ")
        guest_regions.append((owner, guest_ipa, device["mmio_size"], name))
        guest_interrupts.add((owner, virtual_intid))

        merged = device | {
            "owner": owner, "guest_ipa": guest_ipa,
            "virtual_intid": virtual_intid,
        }
        assigned.append(merged)
        parsed[owner]["devices"].append(merged)
    return parsed, assigned


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

def emit_asm(outdir: Path, count: int, digest: str) -> str:
    lines = [
        "// Generated by tools/yml2dtb/yml2dtb.py — do not edit.",
        "// Embeds the per-guest FDT blobs and the lookup table the",
        "// runtime guest_table construction reads at boot.",
        # The digest makes this file's text change whenever any blob
        # changes, so ninja's restat never prunes the recompile that
        # re-reads the .incbin payloads.
        f"// blobs sha256: {digest}",
        "",
        '    .section .rodata.guest_dtb, "a"',
    ]
    for i in range(count):
        lines += [
            "    .balign 8",
            f"guest_dtb_{i}_start:",
            f'    .incbin "{outdir}/guest{i}.dtb"',
            f"guest_dtb_{i}_end:",
        ]
    lines += [
        "",
        "    .balign 8",
        "    .global g_guest_dtbs",
        "g_guest_dtbs:",
    ]
    for i in range(count):
        lines += [f"    .quad guest_dtb_{i}_start", f"    .quad guest_dtb_{i}_end"]
    lines += [
        "",
        "    .global g_guest_dtb_count",
        "g_guest_dtb_count:",
        f"    .word {count}",
        "",
    ]
    return "\n".join(lines)


def emit_array(type_name: str, name: str, values: list[str]) -> list[str]:
    if not values:
        return [f"inline constexpr std::array<{type_name}, 0> {name}{{}};", ""]
    return [
        f"inline constexpr std::array<{type_name}, {len(values)}> {name}{{{{",
        *(f"    {value}," for value in values),
        "}};",
        "",
    ]


def emit_device_policy(inventory: dict, assigned: list[dict]) -> str:
    assignments = [
        ("nova::dma::Assignment{.device_id = %d, .stream_id = 0x%XU, .vm = %d}"
         % (device["device_id"], stream, device["owner"]))
        for device in assigned for stream in device["streams"]
    ]
    streams = [
        ("nova::dma::DeviceStream{.device_id = %d, .stream_id = 0x%XU}"
         % (device["device_id"], stream))
        for device in inventory["devices"] for stream in device["streams"]
    ]
    regions = [
        ("nova::dma::DeviceRegion{.device_id = %d, .ipa_base = 0x%XULL, "
         ".pa_base = 0x%XULL, .size = 0x%XULL}"
         % (device["device_id"], device["guest_ipa"], device["mmio_base"],
            device["mmio_size"]))
        for device in assigned
    ]
    interrupts = [
        ("nova::dma::DeviceInterrupt{.device_id = %d, .physical_intid = %d, "
         ".virtual_intid = %d, .trigger = nova::dma::InterruptTrigger::k%s}"
         % (device["device_id"], device["physical_intid"],
            device["virtual_intid"], device["trigger"].capitalize()))
        for device in assigned
    ]
    lines = [
        "#pragma once",
        "",
        "// Generated device ownership for the selected board and project.",
        "#include \"nova/abi/dma.hpp\"",
        "",
        "#include <array>",
        "",
        "namespace nova::generated {",
        "",
    ]
    lines += emit_array("nova::dma::Assignment", "kDmaAssignments", assignments)
    lines += emit_array("nova::dma::DeviceStream", "kDeviceStreams", streams)
    lines += emit_array("nova::dma::DeviceRegion", "kDeviceRegions", regions)
    lines += emit_array("nova::dma::DeviceInterrupt", "kDeviceInterrupts", interrupts)
    lines += ["} // namespace nova::generated", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config", type=Path, help="guest configuration YAML")
    ap.add_argument("-o", "--outdir", type=Path, required=True)
    ap.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT,
                    help="guest_layout.h to validate against")
    ap.add_argument("--board-layout", type=Path, required=True,
                    help="selected board_layout.h")
    ap.add_argument("--inventory", type=Path, required=True,
                    help="selected board device inventory")
    args = ap.parse_args()

    layout = read_layout(args.layout, args.board_layout)
    inventory = load_inventory(args.inventory, layout)
    guests, assigned = load_config(args.config, layout, inventory)

    args.outdir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    for i, guest in enumerate(guests):
        blob = build_guest_dtb(guest, layout)
        if len(blob) > layout["NOVA_GUEST_DTB_SIZE"]:
            sys.exit(f"yml2dtb: {guest['name']}: DTB {len(blob)} bytes exceeds the "
                     f"{layout['NOVA_GUEST_DTB_SIZE']:#x} guest window reservation")
        (args.outdir / f"guest{i}.dtb").write_bytes(blob)
        digest.update(blob)
        # Round-trip through dtc when available — catches malformed
        # structure the byte-level writer cannot see. Optional so the
        # build works without dtc installed.
        if shutil.which("dtc"):
            r = subprocess.run(
                ["dtc", "-I", "dtb", "-O", "dts", "-o", "/dev/null",
                 str(args.outdir / f"guest{i}.dtb")],
                capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f"yml2dtb: {guest['name']}: dtc rejected the blob:\n{r.stderr}")
    (args.outdir / "guest_dtbs.S").write_text(
        emit_asm(args.outdir.resolve(), len(guests), digest.hexdigest()))
    (args.outdir / "device_policy.hpp").write_text(
        emit_device_policy(inventory, assigned))
    print(f"yml2dtb: {args.config} -> {len(guests)} guest DTB(s), "
          f"{len(assigned)} device assignment(s) in {args.outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
