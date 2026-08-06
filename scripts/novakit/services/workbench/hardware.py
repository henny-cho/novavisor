"""The board map: the hardware structure the UI draws, read from the
headers that already define it.

One machine description, two renderings. `image/dtb.py` turns these
values into a device tree the guest boots on; this turns them into the
picture a reader looks at. Nothing below is typed in twice — porting to
another board edits `board_layout.h` and `device_inventory.yml`, and both
renderings follow.

The map is structure only: addresses, sizes and interrupt numbers that
hold for the machine whatever runs on it. Anything that changes per run
(who owns a device, which vCPU is resident) is measured elsewhere and
joined by the UI.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ...core import config
from ...image import abi, dtb

BOARD_DIR = config.REPO / "src" / "hal" / "board"
DEFAULT_BOARD = "qemu_virt"

# Guest-visible constants the board map states but read_layout does not
# carry: the direct-assignment window.
EXTRA_GUEST_DEFINES = ("NOVA_EDU_BAR0_IPA", "NOVA_EDU_BAR0_SIZE", "NOVA_EDU_SPI")
EXTRA_BOARD_DEFINES = ("NOVA_BOARD_PRISTINE_SIZE",)
# Present only where the board has the peripheral. A board without a
# PCIe aperture draws one block fewer, which is the fact about it.
OPTIONAL_BOARD_DEFINES = ("NOVA_BOARD_PCIE_ECAM_BASE",)

# Region kinds. The UI styles and captions by these, so a new kind needs
# a UI edit; a new *region* of a known kind does not.
KIND_EL2 = "el2"
KIND_GUEST = "guest"
KIND_SHARED = "shared"
KIND_PRISTINE = "pristine"
KIND_HOLE = "hole"
KIND_TRAP = "trap"
KIND_ASSIGNED = "assigned"


def board_layout_header(board: str = DEFAULT_BOARD) -> Path:
    return BOARD_DIR / board / "include" / "hal" / "board" / "active" / "board_layout.h"


def inventory_path(board: str = DEFAULT_BOARD) -> Path:
    return BOARD_DIR / board / "device_inventory.yml"


def platform(board: str = DEFAULT_BOARD) -> dict[str, int]:
    """The board numbers the observation layer needs before there is any
    picture to draw: where the machine's RAM aperture starts (QEMU backs
    exactly that span with the file the bridge mmaps, so a physical
    address is an offset into it), how many PEs the scheduler runs on,
    and where the IVC page sits.

    Same headers `board_map` reads — a value typed into the bridge
    instead would read the wrong offset on any board but this one.
    """
    try:
        return abi.read_defines(
            board_layout_header(board),
            [
                "NOVA_BOARD_PHYS_RAM_BASE",
                "NOVA_BOARD_PHYS_RAM_SIZE",
                "NOVA_BOARD_SMP_CPUS",
                "NOVA_BOARD_IVC_SHM_PA",
            ],
        )
    except ValueError as error:
        sys.exit(f"nova workbench: {error}")


def load_inventory(path: Path) -> dict:
    try:
        import yaml
    except ImportError:  # pragma: no cover - pinned in requirements-cli.txt
        sys.exit("nova workbench: missing PyYAML. Install python3-yaml or PyYAML.")
    return yaml.safe_load(path.read_text()) or {}


def _region(base: int, size: int, kind: str, name: str) -> dict:
    return {"base": base, "size": size, "kind": kind, "name": name}


def _with_holes(base: int, size: int, placed: list[dict]) -> list[dict]:
    """Order the placed regions and name the gaps between them.

    An address strip that shows only what is occupied reads as if the
    machine were full. The gaps are part of the map, so they are regions
    too — computed here rather than left for the UI to infer from
    neighbouring bases.
    """
    regions: list[dict] = []
    cursor = base
    for region in sorted(placed, key=lambda item: item["base"]):
        if region["base"] > cursor:
            regions.append(_region(cursor, region["base"] - cursor, KIND_HOLE, "unused"))
        regions.append(region)
        cursor = max(cursor, region["base"] + region["size"])
    if cursor < base + size:
        regions.append(_region(cursor, base + size - cursor, KIND_HOLE, "unused"))
    return regions


def _physical_regions(values: dict) -> list[dict]:
    """The PA map: what the hypervisor placed in the board's RAM."""
    placed = [
        _region(values["NOVA_BOARD_RAM_BASE"], values["NOVA_BOARD_RAM_SIZE"],
                KIND_EL2, "EL2 image"),
        _region(values["NOVA_BOARD_GUEST_PA_BASE"], values["NOVA_BOARD_GUEST_PA_SIZE"],
                KIND_GUEST, "guest windows"),
        _region(values["NOVA_BOARD_IVC_SHM_PA"], values["NOVA_IVC_SHM_SIZE"],
                KIND_SHARED, "IVC page"),
        _region(values["NOVA_BOARD_PRISTINE_PA"], values["NOVA_BOARD_PRISTINE_SIZE"],
                KIND_PRISTINE, "pristine copies"),
    ]
    return _with_holes(
        values["NOVA_BOARD_PHYS_RAM_BASE"], values["NOVA_BOARD_PHYS_RAM_SIZE"], placed
    )


def _intermediate_regions(values: dict) -> list[dict]:
    """The IPA map every guest sees.

    Not a window with gaps but a set of frames, so no holes are named:
    the space between them is unmapped by construction and any access to
    it aborts rather than trapping into an emulator.
    """
    return sorted(
        [
            _region(values["NOVA_GICD_IPA_BASE"], values["NOVA_GICD_FRAME_SIZE"],
                    KIND_TRAP, "GICD"),
            _region(values["NOVA_GICR_IPA_BASE"],
                    values["NOVA_GICR_FRAME_SIZE"] * values["NOVA_BOARD_SMP_CPUS"],
                    KIND_TRAP, "GICR"),
            _region(values["NOVA_VUART_IPA_BASE"], values["NOVA_VUART_IPA_SIZE"],
                    KIND_TRAP, "vUART"),
            _region(values["NOVA_EDU_BAR0_IPA"], values["NOVA_EDU_BAR0_SIZE"],
                    KIND_ASSIGNED, "EDU BAR0"),
            _region(values["NOVA_GUEST_IPA_BASE"], values["NOVA_GUEST_IPA_SIZE"],
                    KIND_GUEST, "guest window"),
            _region(values["NOVA_IVC_SHM_IPA"], values["NOVA_IVC_SHM_SIZE"],
                    KIND_SHARED, "IVC page"),
        ],
        key=lambda region: region["base"],
    )


def _blocks(values: dict, inventory: dict) -> list[dict]:
    cpus = values["NOVA_BOARD_SMP_CPUS"]
    gicr_frame = values["NOVA_GICR_FRAME_SIZE"]
    blocks = [
        {
            "id": "gicd", "label": "GICD", "layer": "ic",
            "base": values["NOVA_BOARD_GICD_BASE"],
            "size": values["NOVA_GICD_FRAME_SIZE"],
            "note": "distributor · SPI",
        },
    ]
    # One redistributor frame per PE, in PE order: the 12-column grid
    # then lands GICR·n directly under pCPUn without any placement rule.
    blocks += [
        {
            "id": f"gicr{cpu}", "label": f"GICR·{cpu}", "layer": "ic",
            "base": values["NOVA_BOARD_GICR_BASE"] + cpu * gicr_frame,
            "size": gicr_frame, "cpu": cpu, "note": "SGI · PPI",
        }
        for cpu in range(cpus)
    ]
    blocks.append({
        "id": "smmu", "label": "SMMUv3", "layer": "ic",
        "base": values["NOVA_BOARD_SMMU_BASE"],
        "size": values["NOVA_BOARD_SMMU_SIZE"],
        "intids": [
            values["NOVA_BOARD_SMMU_EVENT_INTID"],
            values["NOVA_BOARD_SMMU_CMD_INTID"],
            values["NOVA_BOARD_SMMU_ERROR_INTID"],
        ],
        "sid_bits": inventory.get("sid_bits"),
    })
    blocks.append({
        "id": "uart0", "label": "PL011 UART0", "layer": "dev",
        "base": values["NOVA_BOARD_UART0_BASE"], "size": 0x1000,
        "intid": values["NOVA_BOARD_UART0_INTID"], "owner": "el2",
    })
    for device in inventory.get("devices", []) or []:
        compatible = str(device.get("compatible", ""))
        blocks.append({
            "id": str(device["id"]),
            "label": (compatible.split(",")[-1] or str(device["id"])).upper(),
            "layer": "dev",
            "base": device["mmio"]["base"], "size": device["mmio"]["size"],
            "intid": device["interrupt"]["intid"],
            "streams": list(device.get("streams", [])),
            "device_id": device.get("device_id"),
            "compatible": compatible,
        })
    if "NOVA_BOARD_PCIE_ECAM_BASE" in values:
        blocks.append({
            "id": "ecam", "label": "PCIe ECAM", "layer": "dev",
            "base": values["NOVA_BOARD_PCIE_ECAM_BASE"],
            "note": "low window",
        })
    return blocks


def board_map(board: str = DEFAULT_BOARD) -> dict:
    """The drawable machine description for one board.

    Every value is looked up in a header or the device inventory; a
    renamed define fails here (and in CI) instead of quietly blanking a
    block in the browser.
    """
    layout_header = board_layout_header(board)
    values = dict(dtb.read_layout(abi.GUEST_LAYOUT, layout_header))
    try:
        values |= abi.read_defines(abi.GUEST_LAYOUT, list(EXTRA_GUEST_DEFINES))
        values |= abi.read_defines(layout_header, list(EXTRA_BOARD_DEFINES))
        name = abi.read_string_define(layout_header, "NOVA_BOARD_NAME")
    except ValueError as error:
        sys.exit(f"nova workbench: {error}")
    for define in OPTIONAL_BOARD_DEFINES:
        try:
            values[define] = abi.read_define(layout_header, define)
        except ValueError:
            pass  # the board simply does not have this peripheral
    return {
        "name": name,
        "cpus": values["NOVA_BOARD_SMP_CPUS"],
        "cpu": values["NOVA_BOARD_GUEST_CPU_COMPATIBLE"],
        # Flat vCPU slots are a fixed stride, so the UI maps a scheduler
        # slot back to (vm, index) without a table of its own.
        "max_guests": abi.MAX_GUESTS,
        "vcpu_stride": abi.MAX_VCPUS_PER_VM,
        "dtb_reserve": values["NOVA_GUEST_DTB_SIZE"],
        "blocks": _blocks(values, load_inventory(inventory_path(board))),
        "regions": {
            "pa": _physical_regions(values),
            "ipa": _intermediate_regions(values),
        },
    }
