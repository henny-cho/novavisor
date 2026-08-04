"""The manifest-against-image contract, runnable as a CI step."""

from __future__ import annotations

import sys
from pathlib import Path

from ...core import config
from . import elfsym
from .observations import MAX_CPUS, OBSERVATIONS, timer_slot_labels


def _fields_of(info: elfsym.TypeInfo) -> set[str]:
    while info.kind == "array":
        info = info.element
    return {member.name for member in info.fields} if info.kind == "struct" else set()


def verify_manifest(elf: Path | None = None) -> int:
    """Resolve every observation against the built debug ELF.

    Zero-filled decoding doubles as a layout smoke test: every entry
    must decode without raising, and every declared field selector must
    exist in the DWARF layout.
    """
    path = elf if elf is not None else config.BUILD_ROOT / config.HV_PRESET / "novavisor.elf"
    if not Path(path).is_file():
        print(f"[workbench] manifest check: missing ELF {path}", file=sys.stderr)
        return 1
    index = elfsym.ElfIndex(Path(path))
    failures = 0
    try:
        for obs in OBSERVATIONS:
            try:
                resolved = index.resolve(obs.symbol)
                elfsym.decode(resolved.type, bytes(resolved.size), fields=obs.fields)
                missing = set(obs.fields) - _fields_of(resolved.type)
                if missing:
                    raise KeyError(f"fields not in layout: {sorted(missing)}")
            except (KeyError, ValueError) as error:
                failures += 1
                print(f"[workbench] stale observation {obs.topic}: {error}", file=sys.stderr)

        # The timer labels are derived from board constants; hold them to
        # the image so a resized queue cannot drift past the UI.
        queue = index.resolve("nova::soft_timer::(anonymous)::g_queue").type
        if queue.count != MAX_CPUS or queue.element.count != len(timer_slot_labels()):
            failures += 1
            print(
                "[workbench] timer queue extents diverge from the manifest constants",
                file=sys.stderr,
            )
    finally:
        index.close()
    if failures == 0:
        print(f"[workbench] manifest check: {len(OBSERVATIONS)} observations resolve")
    return 1 if failures else 0
