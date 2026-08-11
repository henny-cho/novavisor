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
    ["NOVA_CMD_WIDX_OFF", "NOVA_CMD_RIDX_OFF", "NOVA_CMD_RECORDS_OFF", "NOVA_CMD_NROWS_OFF"],
)
WIDX_OFF = _OFFSETS["NOVA_CMD_WIDX_OFF"]
RIDX_OFF = _OFFSETS["NOVA_CMD_RIDX_OFF"]
RECORDS_OFF = _OFFSETS["NOVA_CMD_RECORDS_OFF"]
NROWS_OFF = _OFFSETS["NOVA_CMD_NROWS_OFF"]
RAM_BASE = 0x4000_0000
PERIOD_US = 10_000
# What a machine of this shape would publish: one op with two bounded
# arguments, one with a bounded duration, one with a free tag. Built
# from the header's vocabulary so a renamed opcode fails here.
OPS = (
    commands.Op(
        code=commands.OPS["spi"],
        words=2,
        a=commands.Band(kind=commands.ARGS["vm"], lo=0, hi=1),
        b=commands.Band(kind=commands.ARGS["plain"], lo=32, hi=63, default=32),
    ),
    commands.Op(
        code=commands.OPS["slice"],
        words=1,
        a=commands.Band(kind=commands.ARGS["micros"], lo=500, hi=50_000, default=5_000),
    ),
    commands.Op(code=commands.OPS["mark"], words=1),
)


class VocabularyTest(unittest.TestCase):
    def test_every_opcode_the_header_defines_has_a_name(self):
        defined = abi.read_define_family(abi.COMMAND_RING, "NOVA_CMD_OP_")
        self.assertEqual(len(commands.OPS), len(defined))
        for name, code in commands.OPS.items():
            with self.subTest(op=name):
                self.assertEqual(commands.op_name(code), name)

    def test_the_vocabulary_is_named_so_a_destructive_op_has_to_say_so(self):
        # Named rather than counted: the set is a design decision. The
        # three power ops stop a running guest, which is the point of
        # them — a host that cannot stop one cannot raise it either, and
        # each runs the path its guest-facing twin already runs.
        self.assertEqual(
            set(commands.OPS), {"mark", "spi", "slice", "stop", "reset", "start"}
        )

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
        word = op | (result << commands.ANSWER_SHIFT)
        return trace.Record(ts=1, code=events.BY_ID["command"].code, cpu=0, a=word, b=a, c=b)

    def test_the_halves_of_the_answer_come_from_the_abi(self):
        # Spelled on both sides, a moved boundary leaves the reader
        # decoding an opcode out of the verdict's bits.
        source = (
            REPO / "src" / "components" / "service" / "command" / "src" / "command.cpp"
        ).read_text()
        self.assertIn("result << NOVA_CMD_ANSWER_SHIFT", source)
        self.assertIn("command.op <= NOVA_CMD_ANSWER_MASK", source)

    def test_an_opcode_too_wide_for_its_half_is_reported_as_none(self):
        # Truncated it would name some other op against this one's
        # verdict; zero is no opcode, so it reads as unnameable.
        decoded = trace.decode(self._record(0, commands.RESULTS["unknown"]))
        self.assertEqual((decoded["op"], decoded["result"]), ("0", "unknown"))

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

    The page is laid out by the same packer the bridge reads back, so
    what a machine would place and what these tests place cannot differ
    in spelling — only in the values a reader has to judge.
    """

    def __init__(
        self,
        directory: str,
        *,
        version: int = commands.VERSION,
        placed: bool = True,
        ops=OPS,
    ):
        self.path = Path(directory) / "guest-ram"
        # Two pages of slack after it, so a writer that mapped more than
        # it should would find room rather than an error.
        self.page_pa = RAM_BASE + commands.PAGE
        raw = bytearray(commands.PAGE * 4)
        if placed:
            commands.format_page(
                raw,
                commands.PAGE,
                version=version,
                period_us=PERIOD_US,
                ops=ops,
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

    def test_what_a_control_may_offer_is_read_from_the_machine(self):
        # EL2 owns the period and the bands it checks against, so every
        # bound a reader is shown comes from the page this run placed
        # rather than from a constant on this side.
        with tempfile.TemporaryDirectory() as directory:
            writer = Machine(directory).writer()
            self.addCleanup(writer.close)
            self.assertEqual(writer.geometry.period_us, PERIOD_US)
            facts = writer.as_dict()
            self.assertEqual(facts["period_us"], PERIOD_US)
            self.assertEqual([op["name"] for op in facts["ops"]], ["spi", "slice", "mark"])
            # Each op carries as many arguments as it reads, and each
            # one says what it means and what it takes. A panel offers
            # what this machine said, never what this side was built with.
            spi, quantum, mark = facts["ops"]
            self.assertEqual([arg["kind"] for arg in spi["args"]], ["vm", "plain"])
            self.assertEqual((spi["args"][1]["lo"], spi["args"][1]["hi"]), (32, 63))
            self.assertEqual(quantum["args"][0], {
                "kind": "micros", "lo": 500, "hi": 50_000, "default": 5_000, "free": False,
            })
            # A free tag is not a band of nothing: lo > hi says so, and
            # a reader has to be able to tell those apart.
            self.assertTrue(mark["args"][0]["free"])

    def test_an_op_this_build_does_not_carry_out_is_not_offered(self):
        # The header names every opcode the two sides can spell; the
        # rows are what this firmware answers to. A machine that
        # published no row for an op must not have it offered.
        with tempfile.TemporaryDirectory() as directory:
            writer = Machine(directory, ops=OPS[:1]).writer()
            self.addCleanup(writer.close)
            offered = {op["name"] for op in writer.as_dict()["ops"]}
            self.assertEqual(offered, {"spi"})
            self.assertIn("slice", commands.OPS)  # nameable, and still not offered

    def test_more_rows_than_the_page_holds_is_refused(self):
        # The count is the producer's, and this reader trusts none of
        # its numbers: reading past the rows would walk into records.
        with tempfile.TemporaryDirectory() as directory:
            machine = Machine(directory)
            raw = bytearray(machine.path.read_bytes())
            struct.pack_into("<I", raw, commands.PAGE + NROWS_OFF, commands.OPS_CAP + 1)
            machine.path.write_bytes(raw)
            with self.assertRaises(commands.NotFormatted):
                machine.writer()

    def test_a_command_lands_in_its_slot_before_the_index_moves(self):
        with tempfile.TemporaryDirectory() as directory:
            machine = Machine(directory)
            writer = machine.writer()
            self.addCleanup(writer.close)
            self.assertEqual(writer.issue(commands.OPS["spi"], 1, 42), 0)
            self.assertEqual(machine.slot(0), (commands.OPS["spi"], 1, 42))
            self.assertEqual(machine.widx, 1)

    def test_a_word_too_wide_for_the_record_is_refused_not_raised_at_the_socket(self):
        # struct's own failure is neither Full nor ValueError, so it
        # would escape the uplink handler and drop the connection — a
        # command taking down the session that issued it.
        with tempfile.TemporaryDirectory() as directory:
            machine = Machine(directory)
            writer = machine.writer()
            self.addCleanup(writer.close)
            for bad in (1 << 64, -1):
                with self.subTest(word=bad), self.assertRaises(ValueError):
                    writer.issue(commands.OPS["mark"], bad)
            self.assertEqual(machine.widx, 0)

    def test_a_full_ring_refuses_and_writes_nothing(self):
        # The whole point of this direction. A command that vanished is
        # a control that did nothing.
        with tempfile.TemporaryDirectory() as directory:
            machine = Machine(directory)
            writer = machine.writer()
            self.addCleanup(writer.close)
            for _ in range(commands.SLOTS):
                writer.issue(commands.OPS["mark"])
            with self.assertRaises(commands.Full) as refused:
                writer.issue(commands.OPS["mark"], 0xDEAD)
            # Named from the vocabulary EL2 shares: this is the one
            # reason no answering record can carry.
            self.assertIn("full", str(refused.exception))
            self.assertEqual(machine.widx, commands.SLOTS)

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
        # The UI root is only ever read by the HTTP path, which nothing
        # here goes through.
        from novakit.services.workbench.server import Bridge

        bridge = Bridge(ui_root=Path("/nonexistent"))
        bridge.store.drain()
        return bridge

    def _reasons(self, bridge, data: dict) -> list[str]:
        bridge._handle_uplink(
            json.dumps({"topic": "cmd", "data": data, "request_id": "command:1"})
        )
        return [
            frame["data"].get("reason", "")
            for frame in bridge.store.drain()
            if frame["topic"] == "life"
        ]

    def test_an_op_this_build_does_not_name_is_refused(self):
        said = self._reasons(self._bridge(), {"op": "detonate"})
        self.assertTrue(any("unknown op 'detonate'" in text for text in said), said)

    def test_an_argument_that_is_not_a_whole_number_is_refused(self):
        # A float would be truncated by int(), which is the same quiet
        # reinterpretation EL2 refuses rather than narrow.
        for value in ("soon", 1.5, None, True):
            with self.subTest(a=value):
                said = self._reasons(self._bridge(), {"op": "mark", "a": value})
                self.assertTrue(any("whole number" in text for text in said), said)

    def test_a_run_with_no_ring_says_so_rather_than_failing_silently(self):
        # An idle bridge has no machine, so there is no page to write.
        said = self._reasons(self._bridge(), {"op": "mark"})
        self.assertTrue(any("no command ring" in text for text in said), said)


if __name__ == "__main__":
    unittest.main()
