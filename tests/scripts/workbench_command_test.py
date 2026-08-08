"""The command vocabulary, and what a machine says it did with one.

The opcodes and the refusal reasons are one numbering shared with EL2,
so these are about the reader keeping step with the header rather than
about any behaviour of its own.
"""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.image import abi  # noqa: E402
from novakit.services.workbench import commands, events, trace  # noqa: E402

_OFFSETS = abi.read_defines(
    abi.COMMAND_RING,
    ["NOVA_CMD_WIDX_OFF", "NOVA_CMD_RIDX_OFF", "NOVA_CMD_RECORDS_OFF"],
)
WIDX_OFF = _OFFSETS["NOVA_CMD_WIDX_OFF"]
RIDX_OFF = _OFFSETS["NOVA_CMD_RIDX_OFF"]
RECORDS_OFF = _OFFSETS["NOVA_CMD_RECORDS_OFF"]
RAM_BASE = 0x4000_0000
PERIOD_US = 10_000


class VocabularyTest(unittest.TestCase):
    def test_every_opcode_the_header_defines_has_a_name(self):
        defined = abi.read_define_family(abi.COMMAND_RING, "NOVA_CMD_OP_")
        self.assertEqual(len(commands.OPS), len(defined))
        for name, code in commands.OPS.items():
            with self.subTest(op=name):
                self.assertEqual(commands.op_name(code), name)

    def test_the_first_command_set_is_the_three_that_are_reversible(self):
        # Named rather than counted: the set is a design decision, and a
        # destructive opcode arriving in it should have to say so here.
        self.assertEqual(set(commands.OPS), {"mark", "spi", "slice"})

    def test_a_refusal_reads_as_a_reason_not_a_number(self):
        for name in ("ok", "unknown", "range", "state", "full"):
            with self.subTest(result=name):
                self.assertEqual(commands.result_name(commands.RESULTS[name]), name)

    def test_an_opcode_this_build_does_not_know_still_reads(self):
        # EL2 refuses what it cannot carry out and says so in the same
        # record. A reader that stopped on the unknown number would drop
        # the very record explaining the refusal.
        unknown = max(commands.OPS.values()) + 1000
        self.assertEqual(commands.op_name(unknown), str(unknown))


class RecordTest(unittest.TestCase):
    """EL2 answers a command with a trace record and nothing else."""

    def _record(self, op: int, result: int, a: int = 0, b: int = 0) -> trace.Record:
        return trace.Record(
            ts=1, code=events.BY_ID["command"].code, cpu=0, a=op | (result << 16), b=a, c=b
        )

    def test_the_verdict_and_the_opcode_share_one_word(self):
        decoded = trace.decode(self._record(commands.OPS["mark"], commands.RESULTS["ok"], 7, 9))
        self.assertEqual(decoded["event"], "command")
        self.assertEqual(decoded["op"], "mark")
        self.assertEqual(decoded["result"], "ok")
        # The arguments travel unchanged, so a record explains itself
        # without the request that produced it.
        self.assertEqual((decoded["a"], decoded["b"]), (7, 9))

    def test_a_refusal_is_as_readable_as_an_acceptance(self):
        decoded = trace.decode(self._record(99, commands.RESULTS["unknown"]))
        self.assertEqual((decoded["op"], decoded["result"]), ("99", "unknown"))


class Machine:
    """A RAM file with a command page in it, standing in for EL2.

    The page is laid out here rather than by the firmware because these
    tests are about the writer; what makes the two agree is the ABI
    header both read, which is where every offset below comes from.
    """

    def __init__(self, directory: str, *, version: int = commands.VERSION, placed: bool = True):
        self.path = Path(directory) / "guest-ram"
        # Two pages of slack after it, so a writer that mapped more than
        # it should would find room rather than an error.
        self.page_pa = RAM_BASE + commands.PAGE
        raw = bytearray(commands.PAGE * 4)
        if placed:
            struct.pack_into(
                "<QIIII",
                raw,
                commands.PAGE,
                commands.MAGIC,
                version,
                commands.REC_SIZE,
                commands.SLOTS,
                PERIOD_US,
            )
        self.path.write_bytes(raw)

    def writer(self, page_bytes: int = commands.PAGE) -> commands.Writer:
        return commands.Writer(self.path, RAM_BASE, self.page_pa, page_bytes)

    def _at(self, offset: int) -> int:
        return struct.unpack_from("<Q", self.path.read_bytes(), commands.PAGE + offset)[0]

    @property
    def widx(self) -> int:
        return self._at(WIDX_OFF)

    def take(self, count: int) -> None:
        """What EL2's drain does to the page: free the slots."""
        raw = bytearray(self.path.read_bytes())
        struct.pack_into("<Q", raw, commands.PAGE + RIDX_OFF, count)
        self.path.write_bytes(raw)

    def slot(self, index: int) -> tuple[int, int, int]:
        offset = commands.PAGE + RECORDS_OFF + (index % commands.SLOTS) * commands.REC_SIZE
        return struct.unpack_from("<QQQ", self.path.read_bytes(), offset)


class WriterTest(unittest.TestCase):
    def test_the_write_window_is_the_ring_and_nothing_else(self):
        # The boundary on what this process can reach into a running
        # machine is the length of this mapping, not any rule about
        # where it points. QEMU shares the whole of guest RAM; this is
        # what makes that irrelevant.
        with tempfile.TemporaryDirectory() as directory:
            writer = Machine(directory).writer()
            self.addCleanup(writer.close)
            self.assertEqual(len(writer._window), commands.PAGE)

    def test_a_page_that_is_not_a_page_is_refused(self):
        # The image says how big the object really is. A global that
        # outgrew its page would put whatever follows it in the window.
        with tempfile.TemporaryDirectory() as directory:
            machine = Machine(directory)
            with self.assertRaises(commands.NotFormatted):
                machine.writer(page_bytes=commands.PAGE * 2)

    def test_an_unplaced_page_is_a_moment_not_a_verdict(self):
        with tempfile.TemporaryDirectory() as directory:
            machine = Machine(directory, placed=False)
            with self.assertRaises(commands.NotYetFormatted):
                machine.writer()

    def test_a_version_this_writer_does_not_know_stops_it(self):
        # Distinct from the above: asking again will never help, and
        # writing anyway would put rubbish where commands go.
        with tempfile.TemporaryDirectory() as directory:
            machine = Machine(directory, version=commands.VERSION + 1)
            with self.assertRaises(commands.NotFormatted) as caught:
                machine.writer()
            self.assertNotIsInstance(caught.exception, commands.NotYetFormatted)

    def test_the_wait_is_read_from_the_machine(self):
        # EL2 owns the period, so the bound a reader is shown comes from
        # the page rather than from a constant on this side.
        with tempfile.TemporaryDirectory() as directory:
            writer = Machine(directory).writer()
            self.addCleanup(writer.close)
            self.assertEqual(writer.geometry.period_us, PERIOD_US)
            self.assertEqual(writer.as_dict()["period_us"], PERIOD_US)
            self.assertEqual(writer.as_dict()["ops"], sorted(commands.OPS))

    def test_a_command_lands_in_its_slot_before_the_index_moves(self):
        with tempfile.TemporaryDirectory() as directory:
            machine = Machine(directory)
            writer = machine.writer()
            self.addCleanup(writer.close)
            self.assertEqual(writer.issue(commands.OPS["spi"], 1, 42), 0)
            self.assertEqual(machine.slot(0), (commands.OPS["spi"], 1, 42))
            self.assertEqual(machine.widx, 1)

    def test_a_full_ring_refuses_and_writes_nothing(self):
        # The whole point of this direction. A command that vanished is
        # a control that did nothing.
        with tempfile.TemporaryDirectory() as directory:
            machine = Machine(directory)
            writer = machine.writer()
            self.addCleanup(writer.close)
            for _ in range(commands.SLOTS):
                writer.issue(commands.OPS["mark"])
            with self.assertRaises(commands.Full):
                writer.issue(commands.OPS["mark"], 0xDEAD)
            self.assertEqual(machine.widx, commands.SLOTS)
            self.assertEqual(writer.pending(), commands.SLOTS)

            # Depth, not exhaustion: what EL2 takes, the host may fill.
            machine.take(commands.SLOTS)
            self.assertEqual(writer.issue(commands.OPS["mark"], 0xBEEF), commands.SLOTS)
            self.assertEqual(machine.slot(commands.SLOTS), (commands.OPS["mark"], 0xBEEF, 0))


class UplinkTest(unittest.TestCase):
    """What a reader is told when a command cannot be issued.

    Every path here ends in a reason on the wire. Nothing is published
    when one *is* issued: EL2 answers with a trace record, so the
    acknowledgement arrives beside the effects instead of ahead of them.
    """

    def _bridge(self):
        from novakit.services.workbench.server import Bridge

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "index.html").write_text("<title>wb</title>")
        bridge = Bridge(ui_root=root)
        bridge.store.drain()
        return bridge

    def _reasons(self, bridge, data: dict) -> list[str]:
        bridge._handle_uplink(json.dumps({"topic": "cmd", "data": data}))
        return [
            frame["data"].get("reason", "")
            for frame in bridge.store.drain()
            if frame["topic"] == "life"
        ]

    def test_an_op_this_build_does_not_name_is_refused(self):
        said = self._reasons(self._bridge(), {"op": "detonate"})
        self.assertTrue(any("unknown op 'detonate'" in text for text in said), said)

    def test_arguments_that_are_not_numbers_are_refused(self):
        said = self._reasons(self._bridge(), {"op": "mark", "a": "soon"})
        self.assertTrue(any("must be integers" in text for text in said), said)

    def test_a_run_with_no_ring_says_so_rather_than_failing_silently(self):
        # An idle bridge has no machine, so there is no page to write.
        said = self._reasons(self._bridge(), {"op": "mark"})
        self.assertTrue(any("no command ring" in text for text in said), said)


if __name__ == "__main__":
    unittest.main()
