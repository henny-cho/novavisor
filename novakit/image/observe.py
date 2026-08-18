"""What the build asks the image, and the program that asks it.

Answering costs a walk of the whole debug section and the answer cannot
change while the image does not, so the question lives where the build
can read it and the answer is written down once.

Only what a walk must be aimed at is here: named globals with their
layouts, the page-table storage, the enums the UI speaks. What the
symbol table alone answers — a stop point's entry, a region's extent —
stays with the consumer that asks it, since that table travels whole.

Rates and shapes are the bridge's; the two halves meet at the topic.
"""

from __future__ import annotations

import argparse  # noqa: TID251 — the build graph runs this as a program
import functools
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import elfsym, inputs


@dataclass(frozen=True)
class Want:
    """One firmware global to resolve, and the topic it feeds.

    `fields` narrows a struct to the members that travel, and the build
    proves each one exists — a renamed member otherwise resolves fine
    and reaches the bridge as a selector matching nothing.
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
    # One array, three readings: the trap frame is forty words, the
    # syndrome three of them, the EL1 bank another cut. Separate entries
    # let each travel at its own rate.
    Want("ctx.trap", "nova::vcpu::g_vcpus", ("ctx",)),
    Want("ctx.syndrome", "nova::vcpu::g_vcpus", ("ctx",)),
    Want("ctx.el1", "nova::vcpu::g_vcpus", ("el1",)),
    # Both of those shadow registers that live in hardware, so they are
    # the VCPU's state as of this stamp and no earlier.
    Want("ctx.synced", "nova::vcpu::g_vcpus", ("synced_at",)),
    # What the machine was built to run, as the machine built it. The
    # whole array: the entries in use are told from the rest by a vmid,
    # which is reserved at zero.
    Want("vm.table", "nova::(anonymous)::g_table"),
    Want("smp.lifecycle", "nova::smp::g_lifecycle"),
    Want("smp.mode", "nova::smp::g_lifecycle_mode"),
    Want("smp.online", "nova::smp::g_online"),
    Want("smp.mail", "nova::smp::g_mail", ("count",)),
    Want("smp.budget", "nova::vcpu::g_budget"),
    # The only route to injection state: the gdb stub carries no ICH_*.
    Want("vgic.lr", "nova::vgic::(anonymous)::g_cpu", ("lr", "lr_token")),
    Want("vgic.synced", "nova::vgic::(anonymous)::g_cpu", ("synced_at",)),
    # The hop before that one: posted, not yet refilled. refill() moves
    # the token rather than copying it, so this list and the in-flight
    # one are disjoint — which is what makes one snapshot enough.
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
    # The table the SMMU walks, not the policy that built it: a
    # quarantined stream shows as the hardware has it.
    Want("smmu.stream", "nova::smmu::(anonymous)::g_stream_table"),
    Want("dev.watchdog", "nova::(anonymous)::g_update_sequence"),
)


# Page table storage. Extents come from the layout, so a resized pool
# is copied whole without this list changing.
STAGE2_SETS = "nova::(anonymous)::g_stage2_sets"
DMA_TABLES = "nova::smmu::(anonymous)::g_dma_tables"
EL2_ROOT = "nova_el2_l1_root"
EL2_POOL = "(anonymous)::g_pool"
TABLES = (STAGE2_SETS, DMA_TABLES, EL2_ROOT, EL2_POOL)

# Where each walk starts, as the machine holds it: the register the CPU
# is given and the root the SMMU built from. The run's configuration
# would describe a machine that was intended rather than one that booted.
VTTBR = "nova::(anonymous)::g_vttbr"
DMA_CONTEXTS = "nova::smmu::(anonymous)::g_contexts"
DMA_CONTEXT_COUNT = "nova::smmu::(anonymous)::g_context_count"
ROOTS = (VTTBR, DMA_CONTEXTS, DMA_CONTEXT_COUNT)

WALK = TABLES + ROOTS

# Enums whose member names the UI speaks. A table of the same names
# kept elsewhere drifts the first time a class is added.
EC_ENUM = "nova::esr::ExceptionClass"
ENUMS = (EC_ENUM,)


@dataclass(frozen=True)
class View:
    """Every answer this image gives, as plain data holding nothing open.

    Producing it is a walk of the whole debug section; reading it back
    is four milliseconds, which is why the walk belongs to the build.

    `walk` is keyed by symbol rather than topic: the page tables feed no
    observation, and the memory map wants extents, not a reading.
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


@functools.lru_cache(maxsize=1)
def _index(elf: Path, stamp: tuple[int, int]) -> elfsym.ElfIndex:
    """The image's debug information, walked once per image.

    The walk is the expensive half of every question below — seconds, for
    an answer that cannot change while the file does not. `stamp` carries
    the image's identity, so a relinked ELF is a different key and is
    walked again; only one is remembered, because a process asks about the
    image it is working on.

    The cache owns the stream, so the index is not closed here: reading a
    symbol from a closed one would fail, and holding one read-only
    descriptor is the price of not walking the DWARF three times.
    """
    del stamp  # part of the key, not of the walk
    return elfsym.ElfIndex(elf)


def resolve(elf: Path) -> View:
    """Answer every question above against one image.

    Reads the ELF and returns data — runnable anywhere, including a build
    step, which calls it once.

    A question with no answer raises rather than yielding a hole: a
    dropped enum turns exception classes into bare numbers and a renamed
    global blanks a panel, both silently.
    """
    stat = elf.stat()
    index = _index(elf, (stat.st_mtime_ns, stat.st_size))
    resolved = {want.topic: index.resolve(want.symbol) for want in OBSERVED}
    for want in OBSERVED:
        _prove(want, resolved[want.topic])
    return View(
        resolved,
        index.symbols,
        {symbol: index.resolve(symbol) for symbol in WALK},
        {want.symbol: resolved[want.topic].address for want in OBSERVED},
        {name: index.enum_labels(name) for name in ENUMS},
    )


def _prove(want: Want, entry: elfsym.ResolvedSymbol) -> None:
    """Hold one answer to the question that asked for it.

    Resolving proves the name. A renamed member still resolves — the
    global is there — so the members are checked too, and decoding a
    zero-filled extent asks the same of the decoder: a layout it cannot
    walk would fail per reading at run time.
    """
    members = entry.type
    while members.kind == "array":
        members = members.element
    have = {field.name for field in members.fields} if members.kind == "struct" else set()
    missing = sorted(set(want.fields) - have)
    if missing:
        raise KeyError(f"{want.symbol}: no member named {missing}")
    elfsym.decode(entry.type, bytes(entry.size), fields=want.fields)


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

    Carried in the artifact so a reader can tell whether the answer it
    found answers *its* question. Without it, adding a topic leaves an
    artifact that is valid, current for its image, and short a panel.
    """
    return _digest(
        {
            "observed": [[want.topic, want.symbol, list(want.fields)] for want in OBSERVED],
            "walk": list(WALK),
            "enums": list(ENUMS),
        }
    )


def image_id(elf: Path) -> str:
    """A name for the image the answer came from.

    By content: a rebuild writes the same path and a copied tree carries
    the same times.
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


def view_of(elf: Path) -> View:
    """The view beside an image, or why there is none to believe.

    The three readers of a view differ only in how they say a view is
    missing — once, to stderr, or as a failed run — so finding and
    checking it is one function and saying so is each caller's own.
    """
    artifact = artifact_of(elf)
    if not artifact.is_file():
        raise Stale(f"{Path(elf).name} has no observation view beside it: rebuild")
    return load(artifact, elf)


def load(path: Path, elf: Path) -> View:
    """Read a view back, or say why it cannot be believed.

    Three questions: does this reader speak the document, did it come
    from this image, does it answer this question. All three are cheap
    next to the walk they replace and any one failing means rebuild.
    """
    try:
        document = json.loads(Path(path).read_text())
    except ValueError as error:
        raise Stale(f"{path.name} is not a document this reads ({error}): rebuild") from None
    if document.get("format") != FORMAT:
        raise Stale(
            f"{path.name} is format {document.get('format')}, this reads {FORMAT}: rebuild"
        )
    if document.get("image") != image_id(elf):
        raise Stale(f"{path.name} was resolved against a different {Path(elf).name}: rebuild")
    if document.get("request") != request_id():
        raise Stale(f"{path.name} answers an older observation manifest: rebuild")
    try:
        return _view_of(document)
    except (KeyError, TypeError) as error:
        # It carries the three names, so it claims to be this document.
        # Refusing beats decoding a fragment into panels.
        raise Stale(f"{path.name} is missing {error}: rebuild") from None


def _view_of(document: dict) -> View:
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
    # Only what this type has: a scalar carries two keys.
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

    A name this image does not carry stops the build here — earlier than
    a test lane, and at the change that caused it.
    """
    parser = argparse.ArgumentParser(description="Resolve the observation manifest against an image")
    parser.add_argument("--elf", required=True, type=Path, help="the linked image to resolve against")
    parser.add_argument("--out", required=True, type=Path, help="where to write the view")
    parser.add_argument("--depfile", type=Path, help="where to write what this read")
    args = parser.parse_args(argv)

    try:
        view = resolve(args.elf)
    except (KeyError, ValueError) as error:
        # KeyError reprs its argument, which here is already a sentence.
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
