"""Call-graph structure of a linked image, read from the ELF itself.

Answers what `nova inspect` does not: routine size, who calls whom, how
many calls leave through a register, and how much of the image is provably
entered.

Symbols come from pyelftools, because the analysed set is defined by
symbol type and section flags and `nm` conflates them; instructions come
from objdump, because nothing in the pinned environment disassembles A64.

The rules, so a second implementation cannot disagree:

  analysed set   FUNC symbols in executable sections, deduplicated by
                 address. A zero-size member is refused — an extent is
                 what makes attribution possible. Mapping symbols, linker
                 bounds and data are outside the set and ignored.
  attribution    an instruction belongs to the function whose extent holds
                 its address, not to the symbol objdump printed above it.
  edges          `bl` always; `b` only when it leaves the function, which
                 makes it a tail call. Conditional and compare-branches
                 never leave one: -ffunction-sections gives each function
                 its own section.
  indirect       `blr`/`br` is counted, never followed. It is the shape a
                 cib service slot has, and following it would be a guess.
  roots          the ELF entry point, the vector table and its slots'
                 targets, and the chains cib stores into service slots at
                 nexus init.
  unproven       everything else. Not dead code: the rules cannot follow a
                 stored address, so it names what to review by hand.

Inlining is invisible here — an inlined function has no symbol, so its
bytes and edges are the caller's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..core import proc

# Demangled name patterns of the chains cib builds and installs into its
# service slots. Nothing branches to them — `nexus.init()` stores their
# address — so the reachability walk has to be told they are entered.
#
# Not anchored at the start: c++filt prints the return type ahead of a
# *template* function, so the callback chain demangles as "void
# callback::builder<...>::run<...>" while the flow chain, whose run() is
# an ordinary member of a template class, carries no prefix. Matching the
# qualified name at a word boundary reads both.
CIB_CHAIN_PATTERNS = (
    re.compile(r"\bcallback::builder<.*>::run<"),
    re.compile(r"\bflow::graph_builder<.*>::built_flow<.*>::run\b"),
)

# The symbol hardware enters on an exception. Its slots are branches; the
# table itself is a root because the CPU, not a call, arrives at it.
VECTOR_TABLE = "_vector_table"

_SHF_EXECINSTR = 0x4
_STT_FUNC = "STT_FUNC"

# objdump -d --no-show-raw-insn: "  400000:\tbl\t400014 <fx_direct>"
_INSN = re.compile(r"^\s+([0-9a-f]+):\s+(\S+)\s*(.*)$")
_TARGET = re.compile(r"^([0-9a-f]+)\s")


class ContractViolation(Exception):
    """The image cannot answer the questions this module asks of it.

    Raised rather than degraded: a report built from an image without
    symbols, or with a routine whose extent is unknown, would look like
    an answer and be a silence.
    """


@dataclass(frozen=True)
class Function:
    name: str
    address: int
    size: int


@dataclass(frozen=True)
class StructureReport:
    """One image's structure. Every collection is ordered for diffing."""

    functions: dict[str, int]
    edges: dict[str, tuple[str, ...]]
    indirect_sites: dict[str, int]
    roots: tuple[str, ...]
    reachable: frozenset[str]
    unproven: frozenset[str]

    def as_dict(self) -> dict:
        """The report as plain data, sorted, ready to compare or print.

        Sparse on purpose: a function with no edges and no indirect site
        contributes no key, so the document says what is there instead of
        repeating what is not.
        """
        return {
            "roots": sorted(self.roots),
            "functions": dict(sorted(self.functions.items())),
            "edges": {name: list(callees) for name, callees in sorted(self.edges.items())},
            "indirect_sites": dict(sorted(self.indirect_sites.items())),
            "reachable": sorted(self.reachable),
            "unproven": sorted(self.unproven),
        }


def _require_elftools():
    try:
        from elftools.elf.elffile import ELFFile  # noqa: TID251

        return ELFFile
    except ImportError as error:
        raise SystemExit(
            "structure analysis requires the pinned pyelftools package; "
            "run novakit/python-env"
        ) from error


def _read_image(elf: Path) -> tuple[int, list[Function]]:
    """The entry address and the analysed set, straight from the ELF."""
    elffile = _require_elftools()
    with Path(elf).open("rb") as handle:
        image = elffile(handle)
        entry = image.header["e_entry"]
        symtab = image.get_section_by_name(".symtab")
        if symtab is None:
            raise ContractViolation(
                f"{Path(elf).name} carries no symbol table: a stripped image "
                "cannot be attributed to functions"
            )
        executable = {
            index
            for index, section in enumerate(image.iter_sections())
            if section["sh_flags"] & _SHF_EXECINSTR
        }
        found: dict[int, Function] = {}
        zero_size: list[str] = []
        for symbol in symtab.iter_symbols():
            if symbol["st_info"]["type"] != _STT_FUNC:
                continue
            if symbol["st_shndx"] not in executable:
                continue
            if symbol["st_size"] == 0:
                zero_size.append(symbol.name)
                continue
            address = symbol["st_value"]
            # Deduplicate by address: COMDAT copies and aliases are one
            # function. The lowest name wins so two runs agree.
            existing = found.get(address)
            if existing is None or symbol.name < existing.name:
                found[address] = Function(symbol.name, address, symbol["st_size"])

    if zero_size:
        raise ContractViolation(
            f"{Path(elf).name}: {len(zero_size)} function symbol(s) carry no size "
            f"({', '.join(sorted(zero_size)[:4])}) — declare .type/.size on them, "
            "or their instructions belong to no function"
        )
    if not found:
        raise ContractViolation(f"{Path(elf).name} holds no sized function symbols")
    return entry, sorted(found.values(), key=lambda fn: fn.address)


def _containing(functions: list[Function], address: int) -> str | None:
    """The function whose extent holds this address, or None.

    Binary search over the address-sorted set: the walk visits every
    instruction, so the lookup is the hot part of the analysis.
    """
    low, high = 0, len(functions) - 1
    while low <= high:
        mid = (low + high) // 2
        function = functions[mid]
        if address < function.address:
            high = mid - 1
        elif address >= function.address + function.size:
            low = mid + 1
        else:
            return function.name
    return None


def _disassemble(elf: Path) -> str:
    result = proc.run(
        ["aarch64-none-elf-objdump", "-d", "--no-show-raw-insn", str(elf)],
        capture=True,
    )
    return result.stdout


def _demangle(names: list[str]) -> dict[str, str]:
    if not names:
        return {}
    result = proc.run(["aarch64-none-elf-c++filt"], capture=True, stdin="\n".join(names))
    readable = result.stdout.splitlines()
    if len(readable) != len(names):
        raise ContractViolation(
            f"c++filt answered {len(readable)} names for {len(names)}: the chain "
            "patterns cannot be matched against a partial demangling"
        )
    return dict(zip(names, readable, strict=True))


def _walk(text: str, functions: list[Function]) -> tuple[dict, dict, dict]:
    """Edges, indirect-site counts, and each function's outward branches.

    The third is kept apart because the vector table's slots are branches
    that make roots rather than edges, and both come from the same pass.
    """
    edges: dict[str, set[str]] = {}
    indirect: dict[str, int] = {}
    branches: dict[str, list[str]] = {}
    extent = {fn.name: (fn.address, fn.address + fn.size) for fn in functions}

    for line in text.splitlines():
        match = _INSN.match(line)
        if match is None:
            continue
        address, mnemonic, operands = int(match.group(1), 16), match.group(2), match.group(3)
        here = _containing(functions, address)
        if here is None:
            continue
        if mnemonic in ("blr", "br"):
            indirect[here] = indirect.get(here, 0) + 1
            continue
        if mnemonic not in ("bl", "b"):
            continue
        target = _TARGET.match(operands)
        if target is None:
            continue
        goes_to = int(target.group(1), 16)
        there = _containing(functions, goes_to)
        if there is None:
            continue
        if mnemonic == "b":
            start, end = extent[here]
            if start <= goes_to < end:
                continue  # a loop inside the function, not an edge
            branches.setdefault(here, []).append(there)
        edges.setdefault(here, set()).add(there)

    return edges, indirect, branches


def _roots(entry: int, functions: list[Function], branches: dict[str, list[str]]) -> list[str]:
    found: set[str] = set()
    at_entry = _containing(functions, entry)
    if at_entry is None:
        raise ContractViolation(
            f"entry point 0x{entry:x} lies in no sized function: the image "
            "cannot say where it starts"
        )
    found.add(at_entry)

    names = [fn.name for fn in functions]
    if VECTOR_TABLE in names:
        found.add(VECTOR_TABLE)
        found.update(branches.get(VECTOR_TABLE, ()))

    demangled = _demangle([name for name in names if name.startswith("_Z")])
    for name, readable in demangled.items():
        if any(pattern.search(readable) for pattern in CIB_CHAIN_PATTERNS):
            found.add(name)
    return sorted(found)


def analyse(elf: Path) -> StructureReport:
    """The structure of one linked image, or a refusal to guess at it."""
    entry, functions = _read_image(elf)
    edges, indirect, branches = _walk(_disassemble(elf), functions)
    roots = _roots(entry, functions, branches)

    reachable: set[str] = set()
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        reachable.add(name)
        pending.extend(edges.get(name, ()))

    every = {fn.name for fn in functions}
    return StructureReport(
        functions={fn.name: fn.size for fn in functions},
        edges={name: tuple(sorted(callees)) for name, callees in edges.items()},
        indirect_sites=dict(indirect),
        roots=tuple(roots),
        reachable=frozenset(reachable),
        unproven=frozenset(every - reachable),
    )
