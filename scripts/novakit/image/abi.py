"""Firmware constants, read from the headers that define them.

The platform headers keep their constants as plain #defines so the
assembler, the linker script, the C/C++ compilers and this reader all see
one definition. Everything that accepts a guest specification — the demo
manifest loader and the DTB generator — validates through here, so a
configuration the hypervisor cannot honour is rejected the same way
whichever entry point reads it first.

Headers no assembler includes spell the same constants as C++ `inline
constexpr`, often as expressions over the ones above them;
`read_constexprs` reads those. Either way the header is the definition
and this file is a reader.
"""

from __future__ import annotations

import ast
import operator
import re
import sys
from pathlib import Path

from ..core.config import REPO

GUEST_LAYOUT = REPO / "src" / "nova" / "abi" / "guest_layout.h"
IVC_RING = REPO / "src" / "nova" / "abi" / "ivc_ring.h"
TRACE_RING = REPO / "src" / "nova" / "abi" / "trace_ring.h"
COMMAND_RING = REPO / "src" / "nova" / "abi" / "command_ring.h"
TELEMETRY = REPO / "src" / "nova" / "abi" / "telemetry.h"
TELEMETRY_COMPONENT = (
    REPO / "src" / "components" / "service" / "telemetry" / "include" / "telemetry" / "telemetry.hpp"
)
DMA = REPO / "src" / "nova" / "abi" / "dma.hpp"


def read_defines(path: Path, wanted: list[str]) -> dict[str, int]:
    """Pull integer #define constants from a platform header."""
    text = path.read_text()
    values: dict[str, int] = {}
    for name in wanted:
        match = re.search(
            rf"^#define\s+{re.escape(name)}\s+(0[xX][0-9a-fA-F]+|\d+)",
            text,
            re.MULTILINE,
        )
        if match is None:
            raise ValueError(f"{name} not found in {path}")
        values[name] = int(match.group(1), 0)
    return values


def read_define(path: Path, name: str) -> int:
    return read_defines(path, [name])[name]


def read_define_family(path: Path, prefix: str) -> dict[str, int]:
    """Every integer #define whose name starts with `prefix`.

    For a family that grows. Naming each member in a second list is a
    list to forget: a trace event added to the header and not to that
    list arrives on the wire as a number nothing can name, and the
    record is skipped without a word. Read the family and there is one
    place to add to.
    """
    return {
        match.group(1): int(match.group(2), 0)
        for match in re.finditer(
            rf"^#define\s+({re.escape(prefix)}\w+)\s+(0[xX][0-9a-fA-F]+|\d+)",
            path.read_text(),
            re.MULTILINE,
        )
    }


# An integer `inline constexpr` at file scope. The type spellings are a
# whitelist, so a constexpr of another kind is skipped rather than
# folded into a number it never was; a skipped name that mattered
# surfaces as a missing key at the caller.
_CONSTEXPR = re.compile(
    r"^inline constexpr\s+(?:std::)?(?:u?int(?:8|16|32|64)_t|size_t|unsigned|int)\s+"
    r"(\w+)\s*=\s*([^;]+);",
    re.MULTILINE,
)
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
# C++ integer literals carry a width/sign suffix Python has no
# spelling for, and the names around them may be namespace-qualified.
_LITERAL = re.compile(r"\b(0[xX][0-9a-fA-F]+|0[bB][01]+|\d+)[uUlL]*\b")
_QUALIFIER = re.compile(r"\b\w+::")
# What a constant expression in these headers is made of. Bitwise NOT is
# absent on purpose: C++ complements a fixed width where Python's `~`
# goes negative, so the two would quietly disagree.
_OPERATORS = {
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
    ast.BitOr: operator.or_,
    ast.BitAnd: operator.and_,
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    # Division is the same on non-negative operands and only there: C++
    # truncates toward zero where Python floors. _fold() refuses the
    # operands on which the two part rather than leaving them out.
    ast.Div: operator.floordiv,
}


class _Unfoldable(ValueError):
    """An expression this reader cannot turn into a number."""


def _fold(node: ast.expr, known: dict[str, int], where: str) -> int:
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in known:
            raise _Unfoldable(f"{where}: {node.id} is not defined above it")
        return known[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        left, right = _fold(node.left, known, where), _fold(node.right, known, where)
        if type(node.op) is ast.Div and (left < 0 or right <= 0):
            raise _Unfoldable(f"{where}: {left} / {right} does not divide as C++ would")
        return _OPERATORS[type(node.op)](left, right)
    raise _Unfoldable(f"{where}: {ast.unparse(node)} is not an expression this evaluates")


def _evaluate(expression: str, known: dict[str, int], where: str) -> int:
    # One line, unqualified, Python-spelled: a C++ expression does not
    # otherwise parse, and across lines it is not an expression.
    flat = _QUALIFIER.sub("", " ".join(expression.split())).replace("'", "")
    try:
        tree = ast.parse(_LITERAL.sub(lambda match: match.group(1), flat), mode="eval")
    except SyntaxError:
        raise _Unfoldable(f"{where}: {flat} does not parse") from None
    return _fold(tree.body, known, where)


def read_constexprs(
    path: Path, known: dict[str, int] | None = None, wanted: set[str] | None = None
) -> dict[str, int]:
    """Every integer `inline constexpr` in a C++ header, in file order.

    Each expression is folded against the constants declared before it,
    so a header is its own dictionary; `known` seeds names from
    elsewhere. One this cannot fold — an unknown name, a call, an
    operator outside the table — stops the tool, since the alternative
    is a plausible number the firmware never had.

    `wanted` narrows that to a few names, for a header that also holds
    expressions with no Python spelling (a fixed-width complement is the
    standing example — `~` means something else here). What is asked for
    still has to fold, and a name that never appears is an error rather
    than a missing key later.
    """
    values = dict(known or {})
    read: dict[str, int] = {}
    for name, expression in _CONSTEXPR.findall(_COMMENT.sub("", path.read_text())):
        asked = wanted is None or name in wanted
        try:
            values[name] = _evaluate(expression, values, f"{path.name}: {name}")
        except _Unfoldable as error:
            if asked:
                raise SystemExit(str(error)) from None
            continue
        if asked:
            read[name] = values[name]
    missing = set(wanted or ()) - set(read)
    if missing:
        raise SystemExit(f"{path.name}: no constexpr named {sorted(missing)}")
    return read


def read_string_define(path: Path, name: str) -> str:
    match = re.search(
        rf'^#define\s+{re.escape(name)}\s+"([^"]+)"',
        path.read_text(),
        re.MULTILINE,
    )
    if match is None:
        raise ValueError(f"{name} not found in {path}")
    return match.group(1)


LIMITS = read_defines(
    GUEST_LAYOUT,
    ["NOVA_MAX_GUESTS", "NOVA_MAX_VCPUS_PER_VM", "NOVA_GUEST_IPA_BASE"],
)
MAX_GUESTS = LIMITS["NOVA_MAX_GUESTS"]
MAX_VCPUS_PER_VM = LIMITS["NOVA_MAX_VCPUS_PER_VM"]
# Every guest links against this window; its backing PA differs by slot.
GUEST_IPA_BASE = LIMITS["NOVA_GUEST_IPA_BASE"]
UART_KINDS = ("none", "vuart")  # UartKind (nova/abi/guest.hpp)


def validate_guest(where: str, spec: dict) -> int:
    """Reject a guest spec the ABI cannot honour; return its vCPU count.

    `where` names the offending source for the diagnostic (a manifest and
    a guest config reach this from different directions).
    """
    requested = spec.get("vcpus", 1)
    try:
        vcpus = int(requested)
    except (TypeError, ValueError):
        sys.exit(f"{where}: vcpus must be an integer (got {requested!r})")
    if not 1 <= vcpus <= MAX_VCPUS_PER_VM:
        sys.exit(f"{where}: vcpus {vcpus} (supported: 1..{MAX_VCPUS_PER_VM})")

    uart = spec.get("uart", "none")
    if uart not in UART_KINDS:
        sys.exit(f"{where}: uart '{uart}' (supported: {', '.join(UART_KINDS)})")
    return vcpus
