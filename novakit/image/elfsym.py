"""Symbol addresses and type layouts, read from the debug ELF.

Here rather than beside the bridge because the ELF is a build input, in
the tree that holds the layout check and the bundle generator. The type
model and the decoder ship with it because whoever reads bytes at run
time needs the model and not the walk.

Resolution is two-staged on purpose (verified against the real image):
addresses come from .symtab — never stripped, and reachable by
self-mangling the C++ qualified name, so no demangler is needed —
while DWARF supplies only member offsets, sizes, and enum values.
Variables are matched to their DWARF DIE by address (DW_OP_addr), which
sidesteps namespace walking; the definition DIE carries no type of its
own, so DW_AT_specification is followed to the declaring DIE.

The decoded view is strict about enums: a value outside the enumeration
is a torn read, and the whole snapshot is discarded rather than shown.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_DW_OP_ADDR = 0x03
_ENCODING_BOOL = 0x02
_SIGNED_ENCODINGS = (0x05, 0x06, 0x0D)


def _require_elftools():
    try:
        from elftools.elf.elffile import ELFFile  # noqa: TID251

        return ELFFile
    except ImportError as error:
        raise SystemExit(
            "symbol inspection requires the pinned pyelftools package; "
            "run novakit/python-env"
        ) from error


def mangle(qualified: str) -> str:
    """Itanium-mangle a namespaced variable name.

    An anonymous namespace becomes the _GLOBAL__N_1 component and marks
    the whole path internal, which prefixes the terminal name with L —
    the exact shape GCC emits into .symtab.
    """
    parts = qualified.split("::")
    if len(parts) == 1:
        return qualified  # extern "C" or global scope: unmangled
    internal = "(anonymous)" in parts
    encoded = "".join(
        "12_GLOBAL__N_1" if part == "(anonymous)" else f"{len(part)}{part}"
        for part in parts[:-1]
    )
    last = parts[-1]
    return f"_ZN{encoded}{'L' if internal else ''}{len(last)}{last}E"


class TornRead(ValueError):
    """An enum field held a value outside its enumeration."""


def _symtab_of(elffile, path: Path) -> dict[str, tuple[int, int]]:
    section = elffile.get_section_by_name(".symtab")
    if section is None:
        raise SystemExit(f"{path}: no .symtab")
    return {
        symbol.name: (symbol["st_value"], symbol["st_size"])
        for symbol in section.iter_symbols()
        if symbol.name
    }


class SymbolTable:
    """An image's .symtab, and what can be answered from it alone.

    Kept apart from the DWARF view because the questions are different
    sizes. "Does this image carry X?" is a name lookup, and building
    type layouts to answer it would make a capability query depend on
    the very debug information it exists to report the absence of.
    """

    def __init__(self, entries: dict[str, tuple[int, int]]):
        self.entries = entries

    def has(self, qualified: str) -> bool:
        """Is this variable in the image?"""
        return mangle(qualified) in self.entries

    def has_function(self, qualified: str) -> bool:
        """Is this function in the image? Same prefix rule as address_of."""
        prefix = mangle(qualified)
        return any(name.startswith(prefix) for name in self.entries)

    def address_of(self, qualified: str) -> int:
        """A function's entry address, by qualified name.

        A function's mangled name carries its parameter types, which
        only the compiler can spell. The *variable* mangling of the same
        name is exactly the prefix those types follow, and Itanium's
        length-prefixed components mean no shorter name can be a prefix
        of a longer one — so matching on it resolves the entry without
        this reader having to encode C++ types.
        """
        prefix = mangle(qualified)
        matches = sorted(name for name in self.entries if name.startswith(prefix))
        if not matches:
            raise KeyError(f"function not in .symtab: {qualified} ({prefix}...)")
        addresses = {self.entries[name][0] for name in matches}
        if len(addresses) > 1:
            raise KeyError(f"{qualified} is overloaded; cannot pick one: {matches}")
        return addresses.pop()

    def extent_of(self, qualified: str) -> tuple[int, int]:
        """Where a variable lives and how much of it there is.

        The DWARF view answers this too, and with a decoded layout — but
        a caller that only needs somewhere to write does not need the
        layout, and building one would make writing depend on debug
        information the .symtab already covers.
        """
        mangled = mangle(qualified)
        if mangled not in self.entries:
            raise KeyError(f"variable not in .symtab: {qualified} ({mangled})")
        return self.entries[mangled]


@dataclass(frozen=True)
class TypeInfo:
    kind: str  # uint | int | bool | enum | pointer | array | struct
    size: int
    name: str = ""
    fields: tuple[Field, ...] = ()  # struct
    element: TypeInfo | None = None  # array
    count: int = 0  # array
    enumerators: tuple[tuple[int, str], ...] = ()  # enum


@dataclass(frozen=True)
class Field:
    name: str
    offset: int
    type: TypeInfo


@dataclass(frozen=True)
class ResolvedSymbol:
    name: str
    address: int
    size: int
    type: TypeInfo


def decode(info: TypeInfo, view: bytes | memoryview, *, fields: tuple[str, ...] = ()):
    """Bytes to plain data. `fields` restricts a top-level struct decode."""
    if info.kind in ("uint", "pointer"):
        return int.from_bytes(view[: info.size], "little")
    if info.kind == "int":
        return int.from_bytes(view[: info.size], "little", signed=True)
    if info.kind == "bool":
        return view[0] != 0
    if info.kind == "enum":
        value = int.from_bytes(view[: info.size], "little")
        for number, label in info.enumerators:
            if number == value:
                return label
        raise TornRead(f"{info.name}: {value} is not an enumerator")
    if info.kind == "array":
        element = info.element
        return [
            decode(element, view[index * element.size : (index + 1) * element.size], fields=fields)
            for index in range(info.count)
        ]
    if info.kind == "struct":
        return {
            member.name: decode(member.type, view[member.offset : member.offset + member.type.size])
            for member in info.fields
            if not fields or member.name in fields
        }
    raise ValueError(f"undecodable kind: {info.kind}")


class ElfIndex:
    """One parsed image: symtab addresses plus DWARF layouts, cached."""

    def __init__(self, path: Path):
        self.path = Path(path)
        elffile_type = _require_elftools()
        self._stream = self.path.open("rb")
        self._elf = elffile_type(self._stream)
        self.symbols = SymbolTable(_symtab_of(self._elf, self.path))
        self._symbols = self.symbols.entries
        self._variable_dies: dict[int, object] | None = None
        self._enum_dies: dict[str, tuple[tuple[int, str], ...]] | None = None
        self._types: dict[int, TypeInfo] = {}

    def close(self) -> None:
        self._stream.close()

    def resolve(self, qualified: str) -> ResolvedSymbol:
        mangled = mangle(qualified)
        if mangled not in self._symbols:
            raise KeyError(f"symbol not in .symtab: {qualified} ({mangled})")
        address, size = self._symbols[mangled]
        die = self._variables().get(address)
        if die is None:
            raise KeyError(f"no DWARF variable at {address:#x} for {qualified}")
        info = self._type_of(die)
        return ResolvedSymbol(qualified, address, size or info.size, info)

    def enum_labels(self, qualified: str) -> dict[int, str]:
        """The enumerators of a firmware enum, by its qualified name.

        The firmware's own enum is the vocabulary. A table of the same
        names kept anywhere else drifts the first time a class is added,
        and nothing notices — whereas a type that stops existing fails
        here, and the resolve above records that as an absence.
        """
        labels = self._enums().get(qualified)
        if labels is None:
            raise KeyError(f"enumeration not in DWARF: {qualified}")
        return dict(labels)

    # ---------------- DWARF walking ----------------

    def _enums(self) -> dict[str, tuple[tuple[int, str], ...]]:
        if self._enum_dies is None:
            self._walk()
        return self._enum_dies

    def _variables(self) -> dict[int, object]:
        """Address -> variable DIE for every namespace-level definition."""
        if self._variable_dies is None:
            self._walk()
        return self._variable_dies

    def _walk(self) -> None:
        """One pass over the tree for both tables it holds.

        Both questions descend namespaces and class scopes and take what
        is at each level, and the descent is nearly all of the cost.
        Subprogram bodies are skipped for both: a local has no fixed
        address and no scope a caller can name, and they are most of the
        tree.
        """
        variables: dict[int, object] = {}
        enums: dict[str, tuple[tuple[int, str], ...]] = {}

        def walk(die, scope: str) -> None:
            for child in die.iter_children():
                tag = child.tag
                if tag == "DW_TAG_variable":
                    location = child.attributes.get("DW_AT_location")
                    if location is None or not location.value:
                        continue
                    expression = location.value
                    if expression[0] != _DW_OP_ADDR or len(expression) < 9:
                        continue
                    variables[int.from_bytes(bytes(expression[1:9]), "little")] = child
                elif tag == "DW_TAG_enumeration_type":
                    name = _name_of(child)
                    if not name:
                        continue  # an unnamed enum has no name to ask for
                    enums.setdefault(
                        f"{scope}{name}",
                        tuple(
                            (member.attributes["DW_AT_const_value"].value, _name_of(member))
                            for member in child.iter_children()
                            if member.tag == "DW_TAG_enumerator"
                        ),
                    )
                elif tag in ("DW_TAG_namespace", "DW_TAG_structure_type", "DW_TAG_class_type"):
                    name = _name_of(child)
                    walk(child, f"{scope}{name}::" if name else scope)

        for cu in self._elf.get_dwarf_info().iter_CUs():
            walk(cu.get_top_DIE(), "")
        self._variable_dies = variables
        self._enum_dies = enums

    def _type_of(self, die) -> TypeInfo:
        # The two-DIE pattern: the defining DIE holds the location, the
        # declaring DIE (via DW_AT_specification) holds the type. A shape
        # this cannot describe raises ValueError, not KeyError: KeyError
        # means "not in this image", and a reader may skip that.
        while "DW_AT_type" not in die.attributes:
            if "DW_AT_specification" not in die.attributes:
                raise ValueError("variable DIE has neither type nor specification")
            die = die.get_DIE_from_attribute("DW_AT_specification")
        return self._resolve_type(die.get_DIE_from_attribute("DW_AT_type"))

    def _resolve_type(self, die) -> TypeInfo:
        cached = self._types.get(die.offset)
        if cached is not None:
            return cached
        info = self._build_type(die)
        self._types[die.offset] = info
        return info

    def _build_type(self, die) -> TypeInfo:
        tag = die.tag
        if tag in ("DW_TAG_typedef", "DW_TAG_const_type", "DW_TAG_volatile_type"):
            return self._resolve_type(die.get_DIE_from_attribute("DW_AT_type"))
        if tag == "DW_TAG_pointer_type":
            size = die.attributes.get("DW_AT_byte_size")
            return TypeInfo("pointer", 8 if size is None else size.value)
        if tag == "DW_TAG_base_type":
            size = die.attributes["DW_AT_byte_size"].value
            encoding = die.attributes["DW_AT_encoding"].value
            if encoding == _ENCODING_BOOL:
                return TypeInfo("bool", size)
            kind = "int" if encoding in _SIGNED_ENCODINGS else "uint"
            return TypeInfo(kind, size, name=_name_of(die))
        if tag == "DW_TAG_enumeration_type":
            size = die.attributes["DW_AT_byte_size"].value
            enumerators = tuple(
                (child.attributes["DW_AT_const_value"].value, _name_of(child))
                for child in die.iter_children()
                if child.tag == "DW_TAG_enumerator"
            )
            return TypeInfo("enum", size, name=_name_of(die), enumerators=enumerators)
        if tag == "DW_TAG_array_type":
            element = self._resolve_type(die.get_DIE_from_attribute("DW_AT_type"))
            count = 0
            for child in die.iter_children():
                if child.tag != "DW_TAG_subrange_type":
                    continue
                if "DW_AT_count" in child.attributes:
                    count = child.attributes["DW_AT_count"].value
                elif "DW_AT_upper_bound" in child.attributes:
                    count = child.attributes["DW_AT_upper_bound"].value + 1
            return TypeInfo("array", element.size * count, element=element, count=count)
        if tag in ("DW_TAG_structure_type", "DW_TAG_class_type"):
            return self._build_struct(die)
        raise ValueError(f"unsupported DWARF type tag: {tag}")

    def _build_struct(self, die) -> TypeInfo:
        size = die.attributes.get("DW_AT_byte_size")
        members: list[Field] = []
        for child in die.iter_children():
            if child.tag == "DW_TAG_inheritance":
                base = self._resolve_type(child.get_DIE_from_attribute("DW_AT_type"))
                offset = _member_offset(child)
                if base.kind == "struct":
                    members.extend(
                        Field(inherited.name, offset + inherited.offset, inherited.type)
                        for inherited in base.fields
                    )
                else:
                    # The base already collapsed to a value (an unwrapped
                    # __atomic_base): keep it as the inherited payload.
                    members.append(Field("_base", offset, base))
            elif child.tag == "DW_TAG_member" and "DW_AT_data_member_location" in child.attributes:
                members.append(
                    Field(
                        _name_of(child),
                        _member_offset(child),
                        self._resolve_type(child.get_DIE_from_attribute("DW_AT_type")),
                    )
                )
        # Transparent wrappers (std::array's _M_elems, std::atomic's _M_i,
        # single-member models like Ownership or TimerQueue) add depth
        # without information: a lone member at offset 0 IS the value.
        if len(members) == 1 and members[0].offset == 0:
            return members[0].type
        return TypeInfo(
            "struct",
            0 if size is None else size.value,
            name=_name_of(die),
            fields=tuple(members),
        )


def _name_of(die) -> str:
    attribute = die.attributes.get("DW_AT_name")
    return attribute.value.decode() if attribute is not None else ""


def _member_offset(die) -> int:
    attribute = die.attributes.get("DW_AT_data_member_location")
    return 0 if attribute is None else attribute.value
