#!/usr/bin/env python3
"""YAML guest config -> per-guest FDT blobs + embedding assembly.

Reads a guest configuration YAML (configs/*.yml), validates it against
the platform layout (nova/abi/guest_layout.h), and emits:

  <outdir>/guest<N>.dtb   one FDT (v17) per guest, the blob the guest
                          receives in x0 and the hypervisor parses to
                          build its runtime guest table
  <outdir>/guest_dtbs.S   .incbin wrapper exposing g_guest_dtbs[] /
                          g_guest_dtb_count to the hypervisor image

Pure stdlib + PyYAML — no dtc dependency, so CI needs nothing new.
Inspect a blob locally with: dtc -I dtb -O dts <outdir>/guest0.dtb
"""

import argparse
import hashlib
import re
import struct
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
DEFAULT_LAYOUT = REPO / "src" / "nova" / "abi" / "guest_layout.h"

# QEMU virt RAM ceiling (-m 1024 in task.sh run / demo_runner.py):
# 0x4000_0000 + 1 GiB. Pristine copies must stay below it.
RAM_END = 0x8000_0000

MIB = 0x10_0000

# ---------------------------------------------------------------------------
# Layout header
# ---------------------------------------------------------------------------

def read_layout(path: Path) -> dict[str, int]:
    """Pull the #define constants the validator needs from guest_layout.h."""
    wanted = [
        "NOVA_GUEST_IPA_BASE", "NOVA_GUEST_IPA_SIZE", "NOVA_GUEST_PA_STRIDE",
        "NOVA_IVC_SHM_PA", "NOVA_GUEST_PRISTINE_PA",
        "NOVA_GUEST_DTB_SIZE", "NOVA_VUART_IPA_BASE", "NOVA_VUART_SPI",
    ]
    text = path.read_text()
    values: dict[str, int] = {}
    for name in wanted:
        m = re.search(rf"#define\s+{name}\s+(0[xX][0-9a-fA-F]+|\d+)", text)
        if not m:
            sys.exit(f"yml2dtb: {name} not found in {path}")
        values[name] = int(m.group(1), 0)
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


def build_guest_dtb(guest: dict, layout: dict[str, int]) -> bytes:
    ipa_base = layout["NOVA_GUEST_IPA_BASE"]
    w = FdtWriter()
    w.begin_node("")
    w.prop_str("compatible", "novavisor,guest")
    w.prop_str("model", guest["name"])
    w.prop_u32("#address-cells", 2)
    w.prop_u32("#size-cells", 2)

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
    w.prop_str("compatible", "arm,psci-1.0")
    w.prop_str("method", "hvc")
    w.end_node()

    if guest["uart"] == "vuart":
        uart_base = layout["NOVA_VUART_IPA_BASE"]
        w.begin_node(f"uart@{uart_base:x}")
        w.prop_str("compatible", "arm,pl011", "arm,primecell")
        w.prop_u64("reg", uart_base, 0x1000)
        # GIC binding: <SPI intid-32 level-high>
        w.prop_u32("interrupts", 0, layout["NOVA_VUART_SPI"] - 32, 4)
        w.end_node()

    w.begin_node("chosen")
    w.end_node()

    w.end_node()
    return w.finish()


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

def load_config(path: Path, layout: dict[str, int]) -> list[dict]:
    doc = yaml.safe_load(path.read_text())
    guests = doc.get("guests") if isinstance(doc, dict) else None
    if not isinstance(guests, list) or not 1 <= len(guests) <= 4:  # kMaxGuests
        sys.exit(f"yml2dtb: {path}: 'guests' must list 1..4 entries")

    ipa_base = layout["NOVA_GUEST_IPA_BASE"]
    stride = layout["NOVA_GUEST_PA_STRIDE"]
    parsed = []
    for i, g in enumerate(guests):
        name = g.get("name", f"vm{i}")
        size = int(g.get("memory_size", layout["NOVA_GUEST_IPA_SIZE"]))
        vcpus = int(g.get("vcpus", 1))
        uart = g.get("uart", "none")
        if not 1 <= vcpus <= 2:  # kMaxVcpusPerVm
            sys.exit(f"yml2dtb: {name}: vcpus {vcpus} (supported: 1..2)")
        if uart not in ("none", "vuart"):
            sys.exit(f"yml2dtb: {name}: uart '{uart}' (supported: none, vuart)")
        if size < layout["NOVA_GUEST_IPA_SIZE"] or size % MIB:
            sys.exit(f"yml2dtb: {name}: memory_size {size:#x} must be a MiB "
                     f"multiple >= {layout['NOVA_GUEST_IPA_SIZE']:#x} (linker window)")
        load_pa = ipa_base + i * stride
        limit = ipa_base + (i + 1) * stride if i + 1 < len(guests) else layout["NOVA_IVC_SHM_PA"]
        if load_pa + size > limit:
            sys.exit(f"yml2dtb: {name}: window {load_pa:#x}+{size:#x} overlaps "
                     f"{'next PA slot' if i + 1 < len(guests) else 'IVC page'} at {limit:#x}")
        parsed.append({"name": name, "memory_size": size, "vcpus": vcpus, "uart": uart})

    pristine_end = layout["NOVA_GUEST_PRISTINE_PA"] + sum(g["memory_size"] for g in parsed)
    if pristine_end > RAM_END:
        sys.exit(f"yml2dtb: pristine copies end at {pristine_end:#x}, past RAM end {RAM_END:#x}")
    return parsed


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config", type=Path, help="guest configuration YAML")
    ap.add_argument("-o", "--outdir", type=Path, required=True)
    ap.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT,
                    help="guest_layout.h to validate against")
    args = ap.parse_args()

    layout = read_layout(args.layout)
    guests = load_config(args.config, layout)

    args.outdir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    for i, guest in enumerate(guests):
        blob = build_guest_dtb(guest, layout)
        if len(blob) > layout["NOVA_GUEST_DTB_SIZE"]:
            sys.exit(f"yml2dtb: {guest['name']}: DTB {len(blob)} bytes exceeds the "
                     f"{layout['NOVA_GUEST_DTB_SIZE']:#x} guest window reservation")
        (args.outdir / f"guest{i}.dtb").write_bytes(blob)
        digest.update(blob)
    (args.outdir / "guest_dtbs.S").write_text(
        emit_asm(args.outdir.resolve(), len(guests), digest.hexdigest()))
    print(f"yml2dtb: {args.config} -> {len(guests)} guest DTB(s) in {args.outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
