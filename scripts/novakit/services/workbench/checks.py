"""The manifest-against-image contract, runnable as a CI step."""

from __future__ import annotations

import sys
from pathlib import Path

from ...core import config
from . import elfsym, snapshot
from .observations import MAX_CPUS, OBSERVATIONS, timer_slot_labels


def _fields_of(info: elfsym.TypeInfo) -> set[str]:
    while info.kind == "array":
        info = info.element
    return {member.name for member in info.fields} if info.kind == "struct" else set()


def _shape(info: elfsym.TypeInfo) -> str:
    if info.kind == "array":
        return f"{_shape(info.element)}[{info.count}]"
    if info.kind == "struct":
        members = ",".join(member.name for member in info.fields)
        return f"{info.name or 'struct'}{{{members}}}"
    if info.kind in ("enum", "bool"):
        return info.name or info.kind
    return f"{info.kind}{info.size * 8}"


def describe_symbols(elf: Path | None = None) -> int:
    """Print where every observation lives in the image.

    The terminal twin of the S layer: the same manifest, the same
    resolution the poller uses, laid out for a human.
    """
    path = elf if elf is not None else config.BUILD_ROOT / config.HV_PRESET / "novavisor.elf"
    if not Path(path).is_file():
        print(f"[workbench] symbols: missing ELF {path}", file=sys.stderr)
        return 1
    index = elfsym.ElfIndex(Path(path))
    try:
        rows = []
        for obs in OBSERVATIONS:
            if obs.pa is not None:
                layout = snapshot.PAGE_LAYOUTS[obs.layout]
                rows.append((obs.topic, obs.pa, layout.size, obs.rate_hz, _shape(layout)))
                continue
            resolved = index.resolve(obs.symbol)
            picked = obs.fields and f" -> {','.join(obs.fields)}" or ""
            rows.append(
                (
                    obs.topic,
                    resolved.address,
                    resolved.size,
                    obs.rate_hz,
                    _shape(resolved.type) + picked,
                )
            )
    finally:
        index.close()
    width = max(len(row[0]) for row in rows)
    print(f"{'topic':<{width}}  {'address':>10}  {'size':>6}  {'hz':>4}  shape")
    for topic, address, size, rate, shape in rows:
        print(f"{topic:<{width}}  {address:#010x}  {size:>6}  {rate:>4g}  {shape}")
    return 0


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
                if obs.pa is not None:
                    if obs.layout not in snapshot.PAGE_LAYOUTS:
                        raise KeyError(f"unknown page layout: {obs.layout!r}")
                    continue
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
