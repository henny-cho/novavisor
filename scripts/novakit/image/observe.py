"""What the build asks the image: which globals travel, and what feeds
each topic.

Here rather than beside the bridge because the answer is the build's to
produce. Resolving a name to an address and a type layout costs a walk
of the whole debug section, the answer cannot change while the image
does not, and the image is a build output — so the question belongs
where the build can read it, and the answer is written down once.

The descent is only as deep as the expense. A name that the symbol
table alone answers — a stop point's entry address, a region's extent —
does not need its question here: the table is small, complete, and
travels whole, so those questions stay with the consumers that ask
them. What is here is what a walk has to be aimed at: named globals with
their layouts, the page-table storage the memory map measures, and the
enumerations whose member names the UI speaks.

Nothing here says how often a value is sampled or how it is drawn.
Those are the bridge's, and they meet this list at the topic.
"""

from __future__ import annotations

import argparse  # noqa: TID251 — the build graph runs this as a program
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import elfsym, inputs


@dataclass(frozen=True)
class Want:
    """One firmware global to resolve, and the topic it feeds.

    `fields` narrows a struct to the members that travel. It is part of
    the question rather than of presentation: the build proves each one
    exists in the layout, which is what makes a renamed member a build
    failure instead of a blank panel.
    """

    topic: str
    symbol: str
    fields: tuple[str, ...] = ()


OBSERVED: tuple[Want, ...] = (
    Want("sched.cpu", "nova::vcpu::g_sched"),
    Want("sched.slots", "nova::vcpu::g_published_state"),
    Want("sched.run", "nova::vcpu::g_vcpus", ("state",)),
    Want("sched.affinity", "nova::vcpu::g_affinity"),
    Want("sched.valid", "nova::vcpu::g_slot_valid"),
    Want("sched.slice", "nova::vcpu::g_slice_ticks"),
    Want("timer.queue", "nova::soft_timer::(anonymous)::g_queue", ("deadline", "armed")),
    Want("timer.programmed", "nova::soft_timer::(anonymous)::g_programmed"),
    Want("timer.cntvoff", "nova::vcpu::g_cntvoff"),
    Want("vm.generation", "nova::vcpu::g_vm_generation"),
    # One array, three readings. The trap frame is forty words; the
    # syndrome is three of them and the EL1 bank is another cut. Three
    # entries over one symbol is what lets each travel at its own rate
    # without the others riding along.
    Want("ctx.trap", "nova::vcpu::g_vcpus", ("ctx",)),
    Want("ctx.syndrome", "nova::vcpu::g_vcpus", ("ctx",)),
    Want("ctx.el1", "nova::vcpu::g_vcpus", ("el1",)),
    Want("smp.lifecycle", "nova::smp::g_lifecycle"),
    Want("smp.mode", "nova::smp::g_lifecycle_mode"),
    Want("smp.online", "nova::smp::g_online"),
    Want("smp.mail", "nova::smp::g_mail", ("count",)),
    Want("smp.budget", "nova::vcpu::g_budget"),
    # Injection state, and the only route to it: the gdb stub's register
    # set carries no ICH_*, so the EL2 shadow is all there is.
    Want("vgic.lr", "nova::vgic::(anonymous)::g_cpu", ("lr", "lr_token")),
    # The hop before that one: posted by a device, not yet refilled into
    # a register. refill() moves the token rather than copying it, so
    # this list and the in-flight one are disjoint by construction —
    # which is what makes the position readable from a single snapshot.
    Want("vgic.token", "nova::vgic::(anonymous)::g_spi_tokens"),
    Want(
        "vgic.dist",
        "nova::vgic::(anonymous)::g_dist",
        ("ctlr", "spi_group", "spi_enabled", "spi_pending"),
    ),
    Want("vgic.resident", "nova::vgic::(anonymous)::g_resident"),
    Want("vgic.capacity", "nova::vgic::(anonymous)::g_lr_count"),
    Want("dev.uart", "nova::vuart::(anonymous)::g_uart", ("head", "count", "imsc")),
    Want("dev.dma", "nova::dma_device::(anonymous)::g_registry"),
    # What each device stream is allowed to do. Read from the table the
    # SMMU actually walks rather than from the policy that built it, so
    # a quarantined stream shows as the hardware has it.
    Want("smmu.stream", "nova::smmu::(anonymous)::g_stream_table"),
    Want("dev.watchdog", "nova::(anonymous)::g_update_sequence"),
)


# Page table storage. Extents come from the layout, so a resized pool is
# copied whole without this list changing.
STAGE2_SETS = "nova::(anonymous)::g_stage2_sets"
DMA_TABLES = "nova::smmu::(anonymous)::g_dma_tables"
EL2_ROOT = "nova_el2_l1_root"
EL2_POOL = "(anonymous)::g_pool"
TABLES = (STAGE2_SETS, DMA_TABLES, EL2_ROOT, EL2_POOL)

# Where each walk starts, as the machine holds it: the register value
# the CPU is given, and the root the SMMU built its stream table from.
# Read from the run's configuration instead, these would describe a
# machine that was intended rather than one that booted.
VTTBR = "nova::(anonymous)::g_vttbr"
DMA_CONTEXTS = "nova::smmu::(anonymous)::g_contexts"
DMA_CONTEXT_COUNT = "nova::smmu::(anonymous)::g_context_count"
ROOTS = (VTTBR, DMA_CONTEXTS, DMA_CONTEXT_COUNT)

WALK = TABLES + ROOTS

# Firmware enumerations whose member names the UI speaks. The firmware's
# own enum is the vocabulary; a table of the same names kept anywhere
# else drifts the first time a class is added and nothing notices.
EC_ENUM = "nova::esr::ExceptionClass"
ENUMS = (EC_ENUM,)


@dataclass(frozen=True)
class View:
    """Every answer this image gives, as plain data holding nothing open.

    Separable from its use on purpose: producing it is a walk of the
    whole debug section, and reading it back is four milliseconds. That
    gap is the reason the walk belongs to the build.

    `walk` is keyed by symbol rather than topic — the page tables feed no
    observation, and what the memory map wants is where they are and how
    big, not a decoded reading.
    """

    resolved: dict[str, elfsym.ResolvedSymbol]
    symbols: elfsym.SymbolTable
    walk: dict[str, elfsym.ResolvedSymbol] = field(default_factory=dict)
    # Where each observed global lives, for matching against what the
    # firmware says it publishes. Keyed by symbol because that is what a
    # slot names; `resolved` is keyed by topic and four topics share one.
    addresses: dict[str, int] = field(default_factory=dict)
    # Enumerator names by the enum's qualified name, then by value.
    enums: dict[str, dict[int, str]] = field(default_factory=dict)


def resolve(elf: Path) -> View:
    """Answer every question above against one image.

    Opens the ELF, reads it, closes it, and returns data — so the caller
    is free to run this anywhere, including a build step.

    A question with no answer stops here rather than yielding a hole. A
    dropped enum would turn named exception classes into bare numbers and
    a renamed global would blank a panel, both silently; raising is what
    makes the rename a build failure instead.
    """
    index = elfsym.ElfIndex(elf)
    try:
        resolved = {want.topic: index.resolve(want.symbol) for want in OBSERVED}
        return View(
            resolved,
            index.symbols,
            {symbol: index.resolve(symbol) for symbol in WALK},
            {want.symbol: resolved[want.topic].address for want in OBSERVED},
            {name: index.enum_labels(name) for name in ENUMS},
        )
    finally:
        index.close()


# ---------------------------------------------------------------------------
# The written form
# ---------------------------------------------------------------------------

# What the reader below speaks. Bumped when the document's shape changes;
# an older reader refuses rather than interpreting a shape it predates.
FORMAT = 1


class Stale(Exception):
    """The artifact does not answer this question about this image."""


def artifact_of(elf: Path) -> Path:
    """Where the view for an image is written: beside it."""
    return Path(elf).with_suffix(".observe.json")


def request_id() -> str:
    """A name for the question this module asks.

    The artifact carries it so a reader can tell whether the answer it
    found is an answer to its own question. Without it, adding a topic
    leaves an artifact that is valid, current for its image, and missing
    the new panel — which reads as an answer.
    """
    return _digest(
        {
            "observed": [[want.topic, want.symbol, list(want.fields)] for want in OBSERVED],
            "walk": list(WALK),
            "enums": list(ENUMS),
        }
    )


def image_id(elf: Path) -> str:
    """A name for the image the answer came from, by its content.

    By content rather than by path or timestamp: a rebuild writes the
    same path, and a copied tree carries the same times.
    """
    return hashlib.sha256(Path(elf).read_bytes()).hexdigest()


def dumps(view: View, elf: Path) -> str:
    return json.dumps(
        {
            "format": FORMAT,
            "image": image_id(elf),
            "request": request_id(),
            "resolved": {topic: _symbol_json(entry) for topic, entry in view.resolved.items()},
            "walk": {name: _symbol_json(entry) for name, entry in view.walk.items()},
            "addresses": view.addresses,
            "enums": {
                name: {str(value): label for value, label in labels.items()}
                for name, labels in view.enums.items()
            },
            "symbols": {name: list(extent) for name, extent in view.symbols.entries.items()},
        },
        separators=(",", ":"),
    )


def load(path: Path, elf: Path) -> View:
    """Read a view back, or say why it cannot be believed.

    Three questions, each with its own answer: does this reader speak the
    document, did it come from this image, and does it answer this
    question. All three are cheap next to the walk they replace, and any
    one of them failing means the same thing — rebuild.
    """
    document = json.loads(Path(path).read_text())
    if document.get("format") != FORMAT:
        raise Stale(
            f"{path.name} is format {document.get('format')}, this reads {FORMAT}: rebuild"
        )
    if document.get("image") != image_id(elf):
        raise Stale(f"{path.name} was resolved against a different {Path(elf).name}: rebuild")
    if document.get("request") != request_id():
        raise Stale(f"{path.name} answers an older observation manifest: rebuild")
    return View(
        {topic: _symbol_of(entry) for topic, entry in document["resolved"].items()},
        elfsym.SymbolTable(
            {name: tuple(extent) for name, extent in document["symbols"].items()}
        ),
        {name: _symbol_of(entry) for name, entry in document["walk"].items()},
        document["addresses"],
        {
            name: {int(value): label for value, label in labels.items()}
            for name, labels in document["enums"].items()
        },
    )


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _symbol_json(entry: elfsym.ResolvedSymbol) -> dict:
    return {
        "name": entry.name,
        "address": entry.address,
        "size": entry.size,
        "type": _type_json(entry.type),
    }


def _symbol_of(data: dict) -> elfsym.ResolvedSymbol:
    return elfsym.ResolvedSymbol(
        data["name"], data["address"], data["size"], _type_of(data["type"])
    )


def _type_json(info: elfsym.TypeInfo) -> dict:
    # Only what this type has: a scalar carries two keys, and the members
    # a struct does not have are absent rather than empty.
    out: dict = {"kind": info.kind, "size": info.size}
    if info.name:
        out["name"] = info.name
    if info.fields:
        out["fields"] = [
            {"name": member.name, "offset": member.offset, "type": _type_json(member.type)}
            for member in info.fields
        ]
    if info.element is not None:
        out["element"] = _type_json(info.element)
    if info.count:
        out["count"] = info.count
    if info.enumerators:
        out["enumerators"] = [[value, label] for value, label in info.enumerators]
    return out


def _type_of(data: dict) -> elfsym.TypeInfo:
    element = data.get("element")
    return elfsym.TypeInfo(
        data["kind"],
        data["size"],
        name=data.get("name", ""),
        fields=tuple(
            elfsym.Field(member["name"], member["offset"], _type_of(member["type"]))
            for member in data.get("fields", ())
        ),
        element=None if element is None else _type_of(element),
        count=data.get("count", 0),
        enumerators=tuple((value, label) for value, label in data.get("enumerators", ())),
    )


# ---------------------------------------------------------------------------
# The build step
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Resolve the manifest against a freshly linked image and write it.

    Run from the build graph, right after the link. A name this image
    does not carry stops the build here — earlier than a test lane, and
    at the change that caused it.
    """
    parser = argparse.ArgumentParser(description="Resolve the observation manifest against an image")
    parser.add_argument("--elf", required=True, type=Path, help="the linked image to resolve against")
    parser.add_argument("--out", required=True, type=Path, help="where to write the view")
    parser.add_argument("--depfile", type=Path, help="where to write what this read")
    args = parser.parse_args(argv)

    try:
        view = resolve(args.elf)
    except KeyError as error:
        # KeyError renders its argument as a repr, and the argument here
        # is already a sentence.
        print(
            f"[observe] {args.elf.name}: {error.args[0]}\n"
            f"[observe] the observation manifest asks for a name this image does not have",
            file=sys.stderr,
        )
        return 1

    args.out.write_text(dumps(view, args.elf))
    if args.depfile is not None:
        args.depfile.write_text(inputs.depfile(args.out))
    print(
        f"[observe] {len(view.resolved)} topics, {len(view.walk)} tables, "
        f"{len(view.enums)} vocabularies, {len(view.symbols.entries)} symbols"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
