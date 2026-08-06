"""Firmware bit encodings, decoded once, on the bridge.

A raw value that means something other than its number — an all-bits-set
"none", a packed list register — becomes what it means here, before it
reaches the wire. Every reader downstream then sees the fact, and none
of them has to carry a copy of the encoding.

An observation opts in through `Obs.shape`. It is opt-in on purpose: a
bitmap with every bit set means "all", not "none", and only the
observation knows which it is.
"""

from __future__ import annotations

from collections.abc import Callable

from ...core import config
from ...image import abi
from . import elfsym

Shape = Callable[[object, elfsym.TypeInfo], object]

# ICH_LR<n>_EL2 field positions, from the GICv3 register header the
# firmware's delivery logic compiles against. One definition, so there
# is nothing for a test to keep equal — a rename fails here on import.
_LR = abi.read_defines(
    config.REPO / "src" / "nova" / "arch" / "gicv3" / "regs.h",
    [
        "NOVA_ICH_LR_STATE_MASK",
        "NOVA_ICH_LR_GROUP1",
        "NOVA_ICH_LR_EOI",
        "NOVA_ICH_LR_PRIORITY_SHIFT",
        "NOVA_ICH_LR_VINTID_MASK",
    ],
)
_STATE_SHIFT = (_LR["NOVA_ICH_LR_STATE_MASK"] & -_LR["NOVA_ICH_LR_STATE_MASK"]).bit_length() - 1
_STATE = {0: None, 1: "pending", 2: "active", 3: "pending+active"}


def none_if_unset(value: object, info: elfsym.TypeInfo) -> object:
    """Turn the firmware's all-bits-set "none" into null.

    kNoVcpu, kNoOwner, kNoDeadline and kNoResident are all `~0` of their
    own width, so the width comes from the DWARF type and no constant
    has to be read twice. Without this a JSON reader has to guess the
    boundary — past 2^53 the number it receives is not the one sent.
    """
    if info.kind == "array":
        return [none_if_unset(item, info.element) for item in value]
    if info.kind == "struct":
        members = {member.name: member.type for member in info.fields}
        return {
            name: none_if_unset(item, members[name]) if name in members else item
            for name, item in value.items()
        }
    if info.kind in ("uint", "pointer") and value == (1 << (info.size * 8)) - 1:
        return None
    return value


def vgic_inflight(value: object, info: elfsym.TypeInfo) -> object:
    """The list-register shadow as the interrupts it is carrying.

    Sending the array as it stands would be 128 words a snapshot, nearly
    all of them zero: the shadow is sized for the architectural maximum
    of 16 while the machine reports four. What a reader wants is the few
    entries actually in flight, so only those travel — and the change
    gate then fires on injections rather than on rewrites that carry the
    same set.
    """
    del info  # the shape is fixed by the register, not by the type
    per_vcpu = []
    for cpu in value:
        live = []
        for slot, raw in enumerate(cpu["lr"]):
            state = _STATE[(raw & _LR["NOVA_ICH_LR_STATE_MASK"]) >> _STATE_SHIFT]
            if state is None:  # 00: the entry holds nothing
                continue
            live.append(
                {
                    "slot": slot,
                    "vintid": raw & _LR["NOVA_ICH_LR_VINTID_MASK"],
                    "state": state,
                    "group1": bool(raw & _LR["NOVA_ICH_LR_GROUP1"]),
                    "prio": (raw >> _LR["NOVA_ICH_LR_PRIORITY_SHIFT"]) & 0xFF,
                    "eoi": bool(raw & _LR["NOVA_ICH_LR_EOI"]),
                }
            )
        per_vcpu.append(live)
    return per_vcpu
