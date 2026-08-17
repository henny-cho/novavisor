"""Console refinement rules held to the lines the firmware prints.

`TagContractTest` already keeps every "[tag]" mapped. The rules that
refine a tagged line into fields are not covered by that: they match the
message body, and the body is prose. Reword one in the firmware and the
rule stops matching — no test fails, no panel breaks, the event simply
loses its fields and its severity forever.

So the rules are checked against the source: every message the firmware
composes is reconstructed from its console::line call, and every rule
has to match at least one of them.
"""

from __future__ import annotations

import re
import unittest

from novakit.services.workbench import anchors

from tests import REPO

SOURCE = REPO / "src"
# Every way the firmware composes a line. Each is a sequence of pieces:
# string literals interleaved with values.
# Value emitters print a number in place; the rest print their pieces.
VALUES = {"console::write_dec64(": "7", "console::write_hex64(": "1f"}
CALLS = ("console::line(", "console::write_parts(", "console::write(", *VALUES)
STRING = re.compile(r'"((?:[^"\\]|\\.)*)"(?:sv)?\s*$')
WRAPPER = re.compile(r"^\s*(?:std::(?:array|to_array)\s*[({]|\{)|[)}]\s*$")
ARRAY = re.compile(r"(?:std::(?:array|to_array)[^{(]*)?[{(]")
# Escapes the console literals actually use; unicode_escape would mangle
# the em dashes they are full of.
ESCAPES = ((r"\n", "\n"), (r"\t", "\t"), (r"\"", '"'), (r"\\\\", "\\"))
# The console takes typed values, not format specifiers, so a sample of
# the right shape stands in for each.
SAMPLE = (
    (re.compile(r"console::Hex\b"), "1f"),  # 16 lowercase digits, no 0x
    (re.compile(r"console::Dec\b"), "7"),
    # A value this cannot render still has to look like one, or a rule
    # extracting a field from it would be judged stale when it is not.
    (re.compile(r".*", re.S), "7"),
)
TAG_PREFIX = re.compile(r"^\[([a-z_]+)\] ")
# A tag always starts a line, so it also ends the one before it.
TAG_START = re.compile(r"(?=\[[a-z_]+\] )")
LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"')


def _rendered(arguments: list[str]) -> str:
    """One console::line call as the line it would print.

    Arguments are string literals interleaved with typed values; the
    literals are taken as written and each value becomes a sample of its
    own shape, which is all a field-extracting rule needs to match.
    """
    out = []
    for raw in arguments:
        piece = WRAPPER.sub("", raw).strip()
        literal = STRING.search(piece)
        if literal and piece.startswith('"'):
            text = literal.group(1)
            for escape, plain in ESCAPES:
                text = text.replace(escape, plain)
            out.append(text)
            continue
        for pattern, sample in SAMPLE:
            if pattern.search(piece):
                out.append(sample)
                break
        else:
            # A ternary between two literals prints one of them; anything
            # else is a value whose text this cannot know.
            choices = re.findall(r'"((?:[^"\\]|\\.)*)"', piece)
            out.append(choices[0] if choices else "X")
    return "".join(out)


def _scan(text: str, start: int, stop_at_comma: bool) -> tuple[list[str], int]:
    """Walk C++ from `start`, tracking nesting and string literals.

    Returns the top-level argument slices and the index just past the
    closing paren. Arguments hold calls and brace initialisers of their
    own, so neither the commas nor the parens can be found by regex.
    """
    parts, depth, at, quoted, escaped, begin = [], 0, start, False, False, start
    while at < len(text):
        char = text[at]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif quoted:
            pass
        elif char in "({[":
            depth += 1
        elif char in ")}]":
            if depth == 0:
                parts.append(text[begin:at])
                return parts, at + 1
            depth -= 1
        elif char == "," and depth == 0 and stop_at_comma:
            parts.append(text[begin:at])
            begin = at + 1
        at += 1
    return parts, at


def _pieces(arguments: list[str]) -> list[str]:
    """The printable pieces of one call.

    `write_parts` takes them as a single `std::array{...}`, so its one
    argument has to be opened before the pieces inside it are visible.
    """
    if len(arguments) != 1:
        return arguments
    opened = ARRAY.match(arguments[0].strip())
    if not opened:
        return arguments
    inner, _ = _scan(arguments[0].strip(), opened.end(), stop_at_comma=True)
    return inner


def _cut(text: str, into: list[str]) -> None:
    """Split where the firmware splits: at a newline, and at a tag —
    a tag always starts a line, so it also ends the one before it."""
    for part in text.split("\n"):
        into += [
            fragment.strip()
            for fragment in TAG_START.split(part)
            if fragment and fragment.strip()
        ]


def firmware_lines() -> list[str]:
    """Every line the firmware could print, reassembled from its source.

    Two sources, because the firmware has two ways of printing. A
    composed line is a run of calls — `console::write` emits one piece
    at a time — so the calls in a file are rendered in source order and
    concatenated. A line printed whole is just a literal, wherever it
    sits: a banner constant, a bare Uart::write, an .asciz in the boot
    assembler. Both become candidates; a rule need only reach one.
    """
    lines: list[str] = []
    for path in sorted(SOURCE.rglob("*")):
        if path.suffix not in {".cpp", ".hpp", ".h", ".S"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        found: list[tuple[int, str]] = []
        for call in CALLS:
            at = text.find(call)
            while at != -1:
                arguments, end = _scan(text, at + len(call), stop_at_comma=True)
                found.append((at, VALUES.get(call) or _rendered(_pieces(arguments))))
                at = text.find(call, end)
        _cut("".join(piece for _, piece in sorted(found)), lines)
        for literal in LITERAL.findall(text):
            _cut(literal.replace(r"\n", "\n"), lines)
    return lines


class RuleContractTest(unittest.TestCase):
    def setUp(self):
        self.lines = firmware_lines()
        self.assertGreater(len(self.lines), 40, "no console::line calls found under src/")

    def test_every_rule_matches_something_the_firmware_prints(self):
        # A rule that matches nothing is a field set and a severity the
        # event log will never show again.
        for rule in anchors.RULES:
            with self.subTest(tag=rule.tag, pattern=rule.pattern.pattern):
                self.assertTrue(
                    any(self._body(rule, line) for line in self.lines),
                    "no firmware line reaches this rule",
                )

    def test_every_named_group_fills_from_a_real_line(self):
        # Matching is not enough: a group can go empty when a value moves
        # in the message, and the card then shows a field with no value.
        for rule in anchors.RULES:
            if not rule.pattern.groupindex:
                continue
            with self.subTest(tag=rule.tag, pattern=rule.pattern.pattern):
                filled = [
                    found.groupdict()
                    for line in self.lines
                    if (found := self._body(rule, line))
                ]
                self.assertTrue(filled, "no firmware line reaches this rule")
                self.assertTrue(
                    any(all(value for value in fields.values()) for fields in filled),
                    f"every match leaves a group empty: {filled}",
                )

    @staticmethod
    def _body(rule, line):
        """The message a rule sees: the tag prefix is stripped first, and
        a rule for one tag never sees another's line."""
        tagged = TAG_PREFIX.match(line)
        if rule.tag is not None:
            if not tagged or tagged.group(1) != rule.tag:
                return None
            return rule.pattern.search(line[tagged.end() :])
        return rule.pattern.search(line[tagged.end() :] if tagged else line)
