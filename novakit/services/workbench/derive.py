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
from ...image import abi, elfsym, observe
from . import translation

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

# ESR_EL2 field positions, from the header esr.hpp derives its own
# constants from. The class *names* are not here: they come from that
# header's enum through DWARF, which the bridge can already read.
_ESR = abi.read_defines(
    config.REPO / "src" / "nova" / "arch" / "esr_fields.h",
    ["NOVA_ESR_EC_SHIFT", "NOVA_ESR_EC_MASK", "NOVA_ESR_IL_SHIFT", "NOVA_ESR_ISS_MASK"],
)


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


def _binding(token: object) -> dict:
    """The physical interrupt an EoI token is holding open, if any.

    An absent token is not missing data. Three call sites post a virtual
    interrupt and only one binds a token — post_spi_tracked, for a device
    SPI whose physical deactivation is owed until the guest EoIs. A
    timer or a doorbell has no physical interrupt behind it at all, so
    the fields are left out rather than nulled: "no physical origin" and
    "origin unknown" must not look the same.
    """
    if not isinstance(token, dict) or not token.get("generation"):
        return {}
    return {"pintid": token["physical_intid"], "generation": token["generation"]}


def vgic_inflight(value: object, info: elfsym.TypeInfo) -> object:
    """The list-register shadow as the interrupts it is carrying.

    Sending the array as it stands would be 128 words a snapshot, nearly
    all of them zero: the shadow is sized for the architectural maximum
    of 16 while the machine reports four. What a reader wants is the few
    entries actually in flight, so only those travel — and the change
    gate then fires on injections rather than on rewrites that carry the
    same set.

    The EoI token rides with the register it belongs to. An entry and its
    physical binding are one fact, and joining them here means no reader
    has to correlate two topics by slot to learn which silicon an
    interrupt came from.
    """
    del info  # the shape is fixed by the register, not by the type
    per_vcpu = []
    for cpu in value:
        live = []
        tokens = cpu.get("lr_token") or []
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
                | _binding(tokens[slot] if slot < len(tokens) else None)
            )
        per_vcpu.append(live)
    return per_vcpu


def vgic_posted(value: object, info: elfsym.TypeInfo) -> object:
    """SPI tokens bound but not yet taken into a list register.

    This is one hop of the injection path made directly observable.
    post_spi_tracked() writes the token here; refill() *moves* it into
    the vCPU's lr_token, clearing the source. So a valid token is in
    exactly one of the two places at any instant, and which one says
    where the interrupt has got to: still pending in the emulated
    distributor, or already in a register the guest can take.

    Only bound tokens travel, so an idle machine sends an empty list per
    VM and the change gate emits it once.
    """
    del info
    return [
        [
            {
                "spi": index,
                "vintid": token["virtual_intid"],
                "pintid": token["physical_intid"],
                "generation": token["generation"],
            }
            for index, token in enumerate(vm)
            if token.get("generation")
        ]
        for vm in value
    ]


def trap_syndrome(value: object, info: elfsym.TypeInfo) -> object:
    """The last trap each vCPU took, as the three words that identify it.

    The Context panel wants all forty registers twice a second; the board
    wants the syndrome current. One topic cannot serve both, so this one
    keeps the three words that identify a trap and drops the rest — which
    makes the faster poll cost less bandwidth than the slower one it
    split from, not more.

    The class number travels, not a name: the names are the firmware's
    own enum, published once in the topology.

    Frequency is still not claimed. The firmware latches the last trap
    per vCPU, so a sample says what happened, never how often.
    """
    del info
    out = []
    for slot in value:
        context = slot.get("ctx") if isinstance(slot, dict) else None
        raw = int(context.get("esr", 0)) if context else 0
        if not raw:  # nothing has trapped on this slot yet
            out.append(None)
            continue
        out.append(
            {
                "esr": f"{raw:#x}",
                "ec": (raw >> _ESR["NOVA_ESR_EC_SHIFT"]) & _ESR["NOVA_ESR_EC_MASK"],
                "il": (raw >> _ESR["NOVA_ESR_IL_SHIFT"]) & 1,
                "iss": f"{raw & _ESR['NOVA_ESR_ISS_MASK']:#x}",
                "far": f"{int(context.get('far', 0)):#x}",
                "elr": f"{int(context.get('elr', 0)):#x}",
            }
        )
    return out


def syndrome_vocabulary(view: observe.View | None) -> dict[str, dict[int, str]]:
    """Names for the syndrome's class field, out of what the build read.

    A lookup, because reading the enum costs a walk of the whole debug
    section and the build has already done it — so there is nothing here
    to cache and nothing to decide about when a cache went stale.

    No image yields nothing rather than failing: the topology is
    published before the first build finishes, and a class shown as its
    number is still the truth.
    """
    if view is None:
        return {}
    return {"esr_ec": view.enums[observe.EC_ENUM]}


def guest_table(value: object, info: elfsym.TypeInfo) -> object:
    """The guest table as the entries the machine actually built.

    The array is sized for the configuration ceiling and the machine
    fills a prefix, so a vmid tells a built entry from an unused slot —
    zero is reserved and the firmware never issues one.

    Only what places a guest travels; entry point, stack and the DTB
    pointer describe how it starts, which nothing on screen asks.
    """
    del info
    return [
        {
            "vm": index,
            "vmid": entry.get("vmid"),
            "ipa": entry.get("ipa_base"),
            "pa": entry.get("load_pa"),
            "size": entry.get("ipa_size"),
            "vcpus": entry.get("vcpus"),
            "cpu": entry.get("cpu"),
            "uart": entry.get("uart"),
            "auto_start": entry.get("auto_start"),
        }
        for index, entry in enumerate(value if isinstance(value, list) else [])
        if isinstance(entry, dict) and entry.get("vmid")
    ]


def timer_armed(value: object, info: elfsym.TypeInfo) -> object:
    """The soft-timer queue as the deadlines it is actually holding.

    Twenty-two slots per core travelled to report the two that were
    armed — the rest are a kNoDeadline and a false, and every reader
    dropped them on arrival. Only the armed ones travel now, each
    carrying the slot it sits in so its owner label still resolves: the
    slot table is published once in the topology, not per snapshot.

    A deadline is passed through as it stands. An armed slot holding
    kNoDeadline would be a firmware fault, and hiding it would be the
    wrong favour.
    """
    del info
    return [
        [
            {"slot": index, "deadline": slot.get("deadline")}
            for index, slot in enumerate(cpu)
            if slot.get("armed")
        ]
        for cpu in value
    ]


def smmu_streams(value: object, info: elfsym.TypeInfo) -> object:
    """Stream table entries as what each one lets a device do.

    Three states: translating through a VM's own Stage 2 tables, aborted
    so every transaction is refused, or never configured. The root
    travels rather than the VM that owns it, because which VM owns which
    root is published beside this.

    Only configured streams travel — a board declares far more stream IDs
    than a run assigns, and the rest are an all-zero entry.
    """
    del info
    ste = translation.STE
    streams = []
    for stream_id, words in enumerate(value):
        if not words[0] & ste["kValid"]:
            continue
        config = (words[0] & ste["kConfigMask"]) >> ste["kConfigShift"]
        entry = {"stream": stream_id}
        if config == ste["kStage2Only"]:
            # Hex: a root is an address, and past 2^53 a JSON number is
            # no longer the one that was sent.
            entry |= {
                "state": "translate",
                "vmid": words[2] & ste["kVmidMask"],
                "root": f"{words[3] & ste['kS2ttbMask']:#x}",
            }
        elif config == 0:
            # Valid and translating nothing: every transaction refused.
            # What a quarantine after a fault leaves behind.
            entry["state"] = "abort"
        else:
            entry["state"] = f"config:{config:#05b}"
        streams.append(entry)
    return streams
