"""Console text to structured events, as pure functions.

The firmware's log is a de-facto wire format: guest output carries a
"[vmN] " prefix and every hypervisor subsystem prints under a fixed
"[tag] " anchor. Splitting and classifying take a line in and hand
events out — no I/O, no clock — so the whole contract is unit-testable.

Classification is two-staged on purpose. The tag alone yields a badge
(a total mapping, so an unrecognised message under a known tag still
lands in the right subsystem), while regex rules only refine severity
and extract fields — the single fragile part stays small.
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass

from ...core import board
from .taxonomy import Badge, Severity


class LineAssembler:
    """Reassemble pty chunks into lines.

    QEMU delivers arbitrary byte chunks: lines split anywhere, CRLF
    endings, and UTF-8 sequences cut mid-character. Feeding bytes keeps
    the decode boundary here instead of in every caller.
    """

    def __init__(self, max_line: int = 4096):
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._pending = ""
        self._max_line = max_line

    def feed(self, chunk: bytes) -> list[str]:
        text = self._pending + self._decoder.decode(chunk)
        lines = []
        while True:
            head, newline, rest = text.partition("\n")
            if not newline:
                break
            lines.append(head.rstrip("\r"))
            text = rest
        # A runaway unterminated line must not buffer without bound.
        while len(text) >= self._max_line:
            lines.append(text[: self._max_line])
            text = text[self._max_line :]
        self._pending = text
        return lines

    def flush(self) -> list[str]:
        text = self._pending + self._decoder.decode(b"", final=True)
        self._pending = ""
        return [text.rstrip("\r")] if text else []


@dataclass(frozen=True)
class ConsoleLine:
    raw: str
    vm: int | None
    tag: str | None
    text: str


@dataclass(frozen=True)
class Anchor:
    badge: Badge
    severity: Severity
    message: str
    fields: dict[str, str]


@dataclass(frozen=True)
class Rule:
    """Refinement for one tag: fields from named groups, overrides kept
    optional so the tag's badge and the heuristic severity survive."""

    tag: str | None
    pattern: re.Pattern[str]
    badge: Badge | None = None
    severity: Severity | None = None


_VM_PREFIX = re.compile(r"^\[vm(\d+)\] ")
_TAG_PREFIX = re.compile(r"^\[([a-z_]+)\] ")

# Every "[tag]" the firmware prints, mapped totally; the contract test
# scans src/ so a new or renamed tag fails here before it fails a panel.
TAG_BADGES: dict[str, Badge] = {
    "boot": Badge.BOOT,
    "core_gic": Badge.GIC,
    "core_vcpu": Badge.SCHED,
    "demo_hvc": Badge.TRAP,
    "dma": Badge.DMA,
    "mux": Badge.MUX,
    "nova": Badge.BOOT,
    "psci": Badge.PSCI,
    "smmu": Badge.SMMU,
    "smp": Badge.SMP,
    "trap_handler": Badge.TRAP,
    "vgic": Badge.VGIC,
    "vuart": Badge.VUART,
    "watchdog": Badge.WDG,
}

RULES: tuple[Rule, ...] = (
    Rule("boot", re.compile(r"^halt:"), severity=Severity.CRIT),
    Rule("core_gic", re.compile(r"^unclaimed physical IRQ INTID=(?P<intid>\d+)"), badge=Badge.IRQ),
    Rule("dma", re.compile(r"^VM (?P<vm>\d+)")),
    Rule("mux", re.compile(r"^focus vm(?P<vm>\d+)$")),
    # console::Hex prints sixteen digits with no 0x prefix.
    Rule("nova", re.compile(r"^boot pe mpidr=(?P<mpidr>[0-9a-f]+) cores=(?P<cores>\d+)")),
    Rule("psci", re.compile(r"^VM (?P<vm>\d+)")),
    Rule(
        "smmu",
        re.compile(r"^fault type=0x(?P<type>[0-9a-f]+) sid=(?P<sid>\S+)"),
        severity=Severity.WARN,
    ),
    Rule("smp", re.compile(r"^core (?P<core>\d+) online")),
    Rule(
        "smp",
        re.compile(r"^(?:guest|DMA) fault in VM (?P<vm>\d+)"),
        badge=Badge.FAULT,
        severity=Severity.WARN,
    ),
    Rule("trap_handler", re.compile(r"^unknown HVC func_id=0x(?P<func_id>[0-9a-f]+)")),
    Rule("trap_handler", re.compile(r"^unclaimed MMIO access at IPA=0x(?P<ipa>[0-9a-f]+)")),
    Rule("watchdog", re.compile(r"^VM (?P<vm>\d+)")),
    # Untagged anchors: the boot banner and the panic path.
    Rule(None, re.compile(r"^NovaVisor booted$"), badge=Badge.BOOT, severity=Severity.INFO),
    Rule(None, re.compile(r"\[NOVA PANIC\]"), badge=Badge.FAULT, severity=Severity.CRIT),
)

_FATAL = tuple(re.compile(pattern) for pattern in board.FATAL_PATTERNS)

# Severity heuristic: stems, so "fail" covers failed/failure. A rule
# override always wins; this only sorts the long tail.
_WARN_MARKERS = (
    "fail",
    "reject",
    "unavailable",
    "unclaim",
    "unhandled",
    "unknown",
    "unemulatable",
    "mismatch",
    "overflow",
    "quarantin",
    "suppress",
    "deferred",
    "parking",
    "corrupt",
    "invalid",
    "missed",
    "error",
    "no usable",
    "exceeds",
)

_DEMO_EXIT = re.compile(r"^demo_exit code=(\d+)$")


def split(raw: str) -> ConsoleLine:
    match = _VM_PREFIX.match(raw)
    if match:
        return ConsoleLine(raw, int(match.group(1)), None, raw[match.end() :])
    match = _TAG_PREFIX.match(raw)
    if match:
        return ConsoleLine(raw, None, match.group(1), raw[match.end() :])
    return ConsoleLine(raw, None, None, raw)


def _base_severity(line: ConsoleLine) -> Severity:
    if any(pattern.search(line.raw) for pattern in _FATAL):
        return Severity.CRIT
    lowered = line.text.lower()
    if any(marker in lowered for marker in _WARN_MARKERS):
        return Severity.WARN
    return Severity.INFO


def classify(line: ConsoleLine) -> tuple[Anchor, ...]:
    """Guest lines are console-only; unknown tags never raise."""
    if line.vm is not None:
        return ()
    if line.tag is None:
        for rule in RULES:
            if rule.tag is None and rule.pattern.search(line.raw):
                return (Anchor(rule.badge, rule.severity, line.raw, {}),)
        return ()
    badge = TAG_BADGES.get(line.tag)
    if badge is None:
        return ()
    severity = _base_severity(line)
    fields: dict[str, str] = {}
    for rule in RULES:
        if rule.tag != line.tag:
            continue
        match = rule.pattern.search(line.text)
        if match:
            badge = rule.badge or badge
            severity = rule.severity or severity
            fields = {key: value for key, value in match.groupdict().items() if value is not None}
            break
    return (Anchor(badge, severity, line.text, fields),)


def lifecycle(line: ConsoleLine) -> tuple[str, dict] | None:
    """Phase transitions the session state machine consumes."""
    if line.vm is not None:
        return None
    if line.raw == "NovaVisor booted":
        return ("booted", {})
    match = _DEMO_EXIT.match(line.raw)
    if match:
        return ("demo-exit", {"code": int(match.group(1))})
    if "[NOVA PANIC]" in line.raw:
        return ("panic", {"message": line.raw})
    if line.tag == "core_vcpu" and "all VCPUs off" in line.text:
        return ("halted", {})
    return None
