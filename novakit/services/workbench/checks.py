"""What the manifest claims beyond what the build already proved.

The build resolves every observed global against the image it linked and
proves the members that travel exist, so this is not that check again.
It reads the same document and asks what is left: the bridge's own
constants against the image's extents, every stop point a function and
no two of them the same one, and the hand-declared layouts known.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ...core import config
from ...image import elfsym, observe
from . import commands, snapshot
from .events import EVENTS, STOPS
from .observations import (
    MAX_CPUS,
    OBSERVATIONS,
    asserted_names,
    issued_ops,
    timer_slot_labels,
)
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
    try:
        return observe.view_of(path)
    except observe.Stale as error:
        print(f"[workbench] {what}: {error}", file=sys.stderr)
        return None


def describe_symbols(elf: Path | None = None) -> int:
    """Print where every observation lives in the image, and what it lacks.

    The terminal twin of the S layer: the same answers the poller reads,
    including which topics this composition has no component to publish.
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
        resolved = view.resolved.get(obs.topic)
        if resolved is None:
            continue  # this composition publishes no such global
        picked = obs.fields and f" -> {','.join(obs.fields)}" or ""
        rows.append(
            (obs.topic, resolved.address, resolved.size, obs.rate_hz, _shape(resolved.type) + picked)
        )
    width = max(len(row[0]) for row in rows)
    print(f"{'topic':<{width}}  {'address':>10}  {'size':>6}  {'hz':>4}  shape")
    for topic, address, size, rate, shape in rows:
        print(f"{topic:<{width}}  {address:#010x}  {size:>6}  {rate:>4g}  {shape}")
    if view.absent:
        print(f"\nnot in this composition: {', '.join(sorted(view.absent))}")
    return 0


def verify_manifest(elf: Path | None = None) -> int:
    """Hold the bridge's own claims to the image the build answered."""
    view = _read("manifest check", elf)
    if view is None:
        return 1
    failures = 0

    # The full profile links every component, so an absence here is a
    # rename or a deletion rather than a composition. That is what lets
    # a subset profile carry absences without any going unnoticed.
    if view.absent:
        failures += 1
        print(
            f"[workbench] the full profile does not carry {sorted(view.absent)}",
            file=sys.stderr,
        )

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
        _report_census()
    return 1 if failures else 0


def _report_census() -> None:
    """How much of what this can see, anything is ever held to.

    A dial, not a gate. Requiring a predicate per observable would only
    produce predicates written to pass, and a number nobody can read is
    how the gap between what the workbench watches and what CI re-asks
    grew unnoticed in the first place.

    All three layers, always. Counting only the ones this repository has
    started asserting would make the fraction look like progress.
    """
    asserted = asserted_names()
    for layer, names in (
        ("S", [obs.topic for obs in OBSERVATIONS]),
        ("T", [event.id for event in EVENTS]),
    ):
        held = sum(1 for name in names if name in asserted)
        print(
            f"[workbench] {layer}: {len(names)} observable, {held} asserted by a demo, "
            f"{len(names) - held} unasserted"
        )
    # The host's own side: what this build will carry out, against what
    # any run ever asks it to.
    issued = issued_ops()
    sent = sum(1 for op in commands.OPS if op in issued)
    print(
        f"[workbench] C: {len(commands.OPS)} opcodes, {sent} issued by a demo, "
        f"{len(commands.OPS) - sent} never issued"
    )
