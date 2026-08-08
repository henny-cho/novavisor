"""The command vocabulary, and what a machine says it did with one.

The opcodes and the refusal reasons are one numbering shared with EL2,
so these are about the reader keeping step with the header rather than
about any behaviour of its own.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.image import abi  # noqa: E402
from novakit.services.workbench import commands, events, trace  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
