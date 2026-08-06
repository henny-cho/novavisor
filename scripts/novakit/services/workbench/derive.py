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

from . import elfsym

Shape = Callable[[object, elfsym.TypeInfo], object]


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
