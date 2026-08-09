"""What the manifest claims beyond what the build already proved.

The build resolves every observed global against the image it just
linked, proves the members that travel exist, and writes the answers
down; a name that is gone stops the build there. So this is not that
check again. It reads the same document — refusing one that answers a
different question — and asks what is left: that the bridge's own
constants still match the image's extents, that every stop point is a
function and no two of them the same one, and that the layouts declared
by hand are known.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ...core import config
from ...image import elfsym, observe
from . import snapshot
from .events import EVENTS, STOPS
from .observations import MAX_CPUS, OBSERVATIONS, timer_slot_labels
from .paths import EDGES


def _shape(info: elfsym.TypeInfo) -> str:
    if info.kind == "array":
        return f"{_shape(info.element)}[{info.count}]"
    if info.kind == "struct":
        members = ",".join(member.name for member in info.fields)
        return f"{info.name or 'struct'}{{{members}}}"
    if info.kind in ("enum", "bool"):
        return info.name or info.kind
    return f"{info.kind}{info.size * 8}"


def _read(what: str, elf: Path | None) -> observe.View | None:
    """The build's answers for an image, or the reason there are none."""
    path = Path(elf) if elf is not None else config.BUILD_ROOT / config.HV_PRESET / "novavisor.elf"
    if not path.is_file():
        print(f"[workbench] {what}: missing ELF {path}", file=sys.stderr)
        return None
    view = observe.artifact_of(path)
    if not view.is_file():
        print(f"[workbench] {what}: {path.name} has no observation view beside it", file=sys.stderr)
        return None
    try:
        return observe.load(view, path)
    except observe.Stale as error:
        print(f"[workbench] {what}: {error}", file=sys.stderr)
        return None


def describe_symbols(elf: Path | None = None) -> int:
    """Print where every observation lives in the image.

    The terminal twin of the S layer: the same manifest, the same
    answers the poller reads, laid out for a human.
    """
    view = _read("symbols", elf)
    if view is None:
        return 1
    rows = []
    for obs in OBSERVATIONS:
        if obs.pa is not None:
            layout = snapshot.PAGE_LAYOUTS[obs.layout]
            rows.append((obs.topic, obs.pa, layout.size, obs.rate_hz, _shape(layout)))
            continue
        resolved = view.resolved[obs.topic]
        picked = obs.fields and f" -> {','.join(obs.fields)}" or ""
        rows.append(
            (obs.topic, resolved.address, resolved.size, obs.rate_hz, _shape(resolved.type) + picked)
        )
    width = max(len(row[0]) for row in rows)
    print(f"{'topic':<{width}}  {'address':>10}  {'size':>6}  {'hz':>4}  shape")
    for topic, address, size, rate, shape in rows:
        print(f"{topic:<{width}}  {address:#010x}  {size:>6}  {rate:>4g}  {shape}")
    return 0


def verify_manifest(elf: Path | None = None) -> int:
    """Hold the bridge's own claims to the image the build answered."""
    view = _read("manifest check", elf)
    if view is None:
        return 1
    failures = 0

    # Declared by hand because guest memory carries no debug information,
    # so nothing above has checked that the name means anything.
    for obs in OBSERVATIONS:
        if obs.pa is not None and obs.layout not in snapshot.PAGE_LAYOUTS:
            failures += 1
            print(f"[workbench] {obs.topic}: unknown page layout {obs.layout!r}", file=sys.stderr)

    # The timer labels are derived from board constants; hold them to
    # the image so a resized queue cannot drift past the UI.
    queue = view.resolved["timer.queue"].type
    if queue.count != MAX_CPUS or queue.element.count != len(timer_slot_labels()):
        failures += 1
        print(
            "[workbench] timer queue extents diverge from the manifest constants",
            file=sys.stderr,
        )

    # Every stop point must still be a function in this image, and no
    # two of them the same one. An inlined or renamed stop leaves the
    # UI offering a breakpoint that can never be hit; two events at
    # one address are one stop wearing two names, and arming either
    # fires both.
    edge_ids = {edge.id for edge in EDGES}
    entries: dict[int, str] = {}
    for event in EVENTS:
        if event.stop:
            try:
                address = view.symbols.address_of(event.symbol)
            except KeyError as error:
                failures += 1
                print(f"[workbench] stale event {event.id}: {error}", file=sys.stderr)
            else:
                shared = entries.setdefault(address, event.id)
                if shared != event.id:
                    failures += 1
                    print(
                        f"[workbench] events {shared} and {event.id} both stop at {address:#x}",
                        file=sys.stderr,
                    )
        if event.edge and event.edge not in edge_ids:
            failures += 1
            print(
                f"[workbench] event {event.id} lights unknown path {event.edge!r}",
                file=sys.stderr,
            )

    if failures == 0:
        print(
            f"[workbench] manifest check: {len(OBSERVATIONS)} observations and "
            f"{len(observe.WALK)} table symbols answered, {len(STOPS)} stop points resolve"
        )
    return 1 if failures else 0
