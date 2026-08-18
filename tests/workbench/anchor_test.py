"""The console-anchor contract: firmware log text in, taxonomy out."""

from __future__ import annotations

import re
import unittest

from novakit.services.workbench import anchors
from novakit.services.workbench.taxonomy import Badge, Severity
from tests import REPO

# (line, badge, severity, field subset) — verbatim firmware shapes.
CLASSIFY_TABLE = (
    ("[smp] core 1 online", Badge.SMP, Severity.INFO, {"core": "1"}),
    ("[smp] failed to return quiesce ACK", Badge.SMP, Severity.WARN, {}),
    ("[smp] guest fault in VM 1", Badge.FAULT, Severity.WARN, {"vm": "1"}),
    ("[smp] DMA fault in VM 2", Badge.FAULT, Severity.WARN, {"vm": "2"}),
    ("[smp] stop rejected: coordinator mailbox unavailable", Badge.SMP, Severity.WARN, {}),
    ("[watchdog] VM 0 missed its heartbeat window", Badge.WDG, Severity.WARN, {"vm": "0"}),
    ("[mux] focus vm2", Badge.MUX, Severity.INFO, {"vm": "2"}),
    (
        "[core_gic] unclaimed physical IRQ INTID=54 — quarantined",
        Badge.IRQ,
        Severity.WARN,
        {"intid": "54"},
    ),
    (
        "[trap_handler] unknown HVC func_id=0x1500 — SMCCC NOT_SUPPORTED",
        Badge.TRAP,
        Severity.WARN,
        {"func_id": "1500"},
    ),
    ("[trap_handler] unhandled WFx — treated as NOP", Badge.TRAP, Severity.WARN, {}),
    ("[trap_handler] unclaimed MMIO access at IPA=0x9010000", Badge.TRAP, Severity.WARN, {"ipa": "9010000"}),
    ("[vgic] RAZ/WI GICD offset 0x0f30", Badge.VGIC, Severity.INFO, {}),
    ("[vuart] RAZ/WI offset 0x18", Badge.VUART, Severity.INFO, {}),
    ("[smmu] initialization failed: stream table", Badge.SMMU, Severity.CRIT, {}),
    ("[smmu] isolation failure: stray write", Badge.SMMU, Severity.CRIT, {}),
    (
        "[smmu] fault type=0x10 sid=5 iova=0x50000000",
        Badge.SMMU,
        Severity.WARN,
        {"type": "10", "sid": "5"},
    ),
    ("[smmu] stage-2 isolation active", Badge.SMMU, Severity.INFO, {}),
    # As the firmware prints it: console::Hex is sixteen digits, no 0x.
    (
        "[nova] boot pe mpidr=0000000000000000 cores=2 cntfrq=62500000",
        Badge.BOOT,
        Severity.INFO,
        {"mpidr": "0000000000000000", "cores": "2"},
    ),
    ("[boot] halt: EL2 stage-1 map build failed", Badge.BOOT, Severity.CRIT, {}),
    ("[core_vcpu] all VCPUs off — halting", Badge.SCHED, Severity.INFO, {}),
    ("[core_vcpu] VM 1 restored", Badge.SCHED, Severity.INFO, {}),
    ("[psci] VM 1 CPU_ON pending", Badge.PSCI, Severity.INFO, {"vm": "1"}),
    ("[dma] EDU round-trip ok", Badge.DMA, Severity.INFO, {}),
    ("[dma] device configuration failed", Badge.DMA, Severity.WARN, {}),
    ("[demo_hvc] PUTS rejected: buffer outside guest window", Badge.TRAP, Severity.WARN, {}),
    ("NovaVisor booted", Badge.BOOT, Severity.INFO, {}),
    ("[NOVA PANIC] recursive fault inside the panic path", Badge.FAULT, Severity.CRIT, {}),
)


class SplitTest(unittest.TestCase):
    def test_guest_prefix_is_demultiplexed(self):
        line = anchors.split("[vm0] echo: ping")
        self.assertEqual((line.vm, line.tag, line.text), (0, None, "echo: ping"))

    def test_multi_digit_vm(self):
        self.assertEqual(anchors.split("[vm12] tick 4").vm, 12)

    def test_hypervisor_tag(self):
        line = anchors.split("[smp] core 1 online")
        self.assertEqual((line.vm, line.tag, line.text), (None, "smp", "core 1 online"))

    def test_plain_line(self):
        line = anchors.split("NovaVisor booted")
        self.assertEqual((line.vm, line.tag, line.text), (None, None, "NovaVisor booted"))


class ClassifyTest(unittest.TestCase):
    def test_firmware_lines_map_to_taxonomy(self):
        for raw, badge, severity, fields in CLASSIFY_TABLE:
            with self.subTest(line=raw):
                anchor_list = anchors.classify(anchors.split(raw))
                self.assertEqual(len(anchor_list), 1)
                anchor = anchor_list[0]
                self.assertEqual(anchor.badge, badge)
                self.assertEqual(anchor.severity, severity)
                for key, value in fields.items():
                    self.assertEqual(anchor.fields.get(key), value)

    def test_guest_output_is_console_only(self):
        self.assertEqual(anchors.classify(anchors.split("[vm0] echo: ping")), ())

    def test_unknown_tag_never_raises(self):
        self.assertEqual(anchors.classify(anchors.split("[newthing] hello")), ())

    def test_plain_chatter_is_console_only(self):
        self.assertEqual(anchors.classify(anchors.split("demo_exit code=0")), ())


class LifecycleTest(unittest.TestCase):
    def test_phases(self):
        table = (
            ("NovaVisor booted", ("booted", {})),
            ("demo_exit code=0", ("demo-exit", {"code": 0})),
            ("demo_exit code=3", ("demo-exit", {"code": 3})),
            ("[core_vcpu] all VCPUs off — halting", ("halted", {})),
            ("[smp] core 1 online", None),
            ("[vm0] demo_exit code=0", None),
        )
        for raw, expected in table:
            with self.subTest(line=raw):
                self.assertEqual(anchors.lifecycle(anchors.split(raw)), expected)

    def test_panic_carries_its_message(self):
        raw = "[NOVA PANIC] EL2 fatal exception: vector 9"
        self.assertEqual(anchors.lifecycle(anchors.split(raw)), ("panic", {"message": raw}))


class LineAssemblerTest(unittest.TestCase):
    def test_lines_split_across_chunks(self):
        assembler = anchors.LineAssembler()
        self.assertEqual(assembler.feed(b"[vm0] ec"), [])
        self.assertEqual(assembler.feed(b"ho: ping\n[smp] "), ["[vm0] echo: ping"])
        self.assertEqual(assembler.feed(b"core 1 online\n"), ["[smp] core 1 online"])

    def test_crlf_is_stripped(self):
        assembler = anchors.LineAssembler()
        self.assertEqual(assembler.feed(b"NovaVisor booted\r\n"), ["NovaVisor booted"])

    def test_utf8_split_mid_character(self):
        payload = "[core_vcpu] all VCPUs off — halting\n".encode()
        cut = payload.index("—".encode()) + 1  # inside the 3-byte em dash
        assembler = anchors.LineAssembler()
        self.assertEqual(assembler.feed(payload[:cut]), [])
        self.assertEqual(assembler.feed(payload[cut:]), ["[core_vcpu] all VCPUs off — halting"])

    def test_runaway_line_is_flushed_in_slabs(self):
        assembler = anchors.LineAssembler(max_line=8)
        self.assertEqual(assembler.feed(b"abcdefghij"), ["abcdefgh"])
        self.assertEqual(assembler.flush(), ["ij"])

    def test_flush_returns_the_partial_tail(self):
        assembler = anchors.LineAssembler()
        assembler.feed(b"no newline")
        self.assertEqual(assembler.flush(), ["no newline"])
        self.assertEqual(assembler.flush(), [])


class TagContractTest(unittest.TestCase):
    """Log-vocabulary drift in src/ must fail here, not blank a panel."""

    TAG_LITERAL = re.compile(r'"\[([a-z_]+)\] ')

    def test_every_firmware_tag_has_a_badge(self):
        tags: set[str] = set()
        for path in (REPO / "src").rglob("*"):
            if path.suffix not in {".cpp", ".hpp", ".h"}:
                continue
            tags.update(self.TAG_LITERAL.findall(path.read_text(encoding="utf-8", errors="ignore")))
        self.assertTrue(tags, "no firmware log tags found under src/")
        self.assertLessEqual(tags, set(anchors.TAG_BADGES), "unmapped firmware log tags")

    def test_every_badge_is_reachable(self):
        reachable = set(anchors.TAG_BADGES.values())
        reachable.update(rule.badge for rule in anchors.RULES if rule.badge)
        self.assertEqual(reachable, set(Badge))
