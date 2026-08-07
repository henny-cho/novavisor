"""The T-layer reader against a region this test writes itself.

The firmware side is proven in tests/host/trace_ring_test.cpp against a
real concurrent writer. What is left to hold here is the reader's half
of the same contract: that it finds the region without any debug info,
refuses a layout it does not understand instead of decoding it, and
accounts for every record between its cursor and the writer's head.
"""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.image import abi  # noqa: E402
from novakit.services.workbench import events, trace  # noqa: E402

L = abi.read_defines(
    abi.TRACE_RING,
    [
        "NOVA_TRACE_MAGIC",
        "NOVA_TRACE_VERSION",
        "NOVA_TRACE_HEADER_SIZE",
        "NOVA_TRACE_RECORDS_OFF",
        "NOVA_TRACE_REC_SIZE",
        "NOVA_TRACE_SIZE",
        "NOVA_TRACE_MAX_RINGS",
    ],
)
RAM_BASE = 0x4000_0000
# Close to the base on purpose: what matters is the `pa - ram_base`
# arithmetic, and the real board offset would make every fixture a
# half-gigabyte file.
TRACE_PA = 0x4000_1000
CAPACITY = 16  # small enough that lapping is easy to arrange
STRIDE = L["NOVA_TRACE_RECORDS_OFF"] + CAPACITY * L["NOVA_TRACE_REC_SIZE"]


class Region:
    """A trace region on disk, written the way the firmware writes one."""

    def __init__(self, rings: int = 2, *, magic: int | None = None, version: int | None = None,
                 capacity: int = CAPACITY, early: int = 0):
        self.rings = rings
        self.capacity = capacity
        self.path = Path(tempfile.mkstemp(dir="/dev/shm", suffix="-ram")[1])
        size = (TRACE_PA - RAM_BASE) + L["NOVA_TRACE_SIZE"]
        self.buffer = bytearray(size)
        self.offset = TRACE_PA - RAM_BASE
        struct.pack_into(
            "<QIIIIIII", self.buffer, self.offset,
            L["NOVA_TRACE_MAGIC"] if magic is None else magic,
            L["NOVA_TRACE_VERSION"] if version is None else version,
            L["NOVA_TRACE_REC_SIZE"], STRIDE, rings, capacity, 62_500_000, early,
        )
        self.heads = [0] * rings
        self.flush()

    def ring_base(self, ring: int) -> int:
        return self.offset + L["NOVA_TRACE_HEADER_SIZE"] + ring * STRIDE

    def emit(self, ring: int, ts: int, code: int, cpu: int = 0, a: int = 0, b: int = 0, c: int = 0) -> None:
        index = self.heads[ring]
        at = self.ring_base(ring) + L["NOVA_TRACE_RECORDS_OFF"] + (index % self.capacity) * L["NOVA_TRACE_REC_SIZE"]
        struct.pack_into("<QHBBIQQ", self.buffer, at, ts, code, cpu, 0, a, b, c)
        self.heads[ring] = index + 1
        struct.pack_into("<Q", self.buffer, self.ring_base(ring), self.heads[ring])

    def reformat(self) -> None:
        """What a restart looks like to a reader holding a stale cursor."""
        self.heads = [0] * self.rings
        for ring in range(self.rings):
            struct.pack_into("<Q", self.buffer, self.ring_base(ring), 0)

    def flush(self) -> None:
        self.path.write_bytes(bytes(self.buffer))

    def reader(self) -> trace.TraceReader:
        self.flush()
        return trace.TraceReader(self.path, RAM_BASE, TRACE_PA)

    def cleanup(self) -> None:
        self.path.unlink(missing_ok=True)


BIND = events.BY_ID["vgic.bind"].code
TRAP = events.BY_ID["trap"].code


class GeometryTest(unittest.TestCase):
    def test_the_region_describes_itself(self):
        region = Region(rings=2)
        self.addCleanup(region.cleanup)
        reader = region.reader()
        self.addCleanup(reader.close)
        self.assertEqual(reader.geometry.rings, 2)
        self.assertEqual(reader.geometry.capacity, CAPACITY)
        # ts -> seconds has one source and it travels with the geometry.
        self.assertEqual(reader.geometry.freq_hz, 62_500_000)

    def test_the_pre_placement_drop_count_travels_with_the_geometry(self):
        """Events emitted before the region existed have no ring and no
        cursor arithmetic that could recover them. Unpublished, they are
        indistinguishable from a quiet boot."""
        region = Region(early=17)
        self.addCleanup(region.cleanup)
        reader = region.reader()
        self.addCleanup(reader.close)
        self.assertEqual(reader.geometry.early, 17)

    def test_a_wrong_magic_is_refused_not_decoded(self):
        """Decoding anyway would turn a version skew into events that
        look plausible and are not."""
        region = Region(magic=0xDEAD)
        self.addCleanup(region.cleanup)
        with self.assertRaises(trace.NotFormatted):
            region.reader()

    def test_a_future_version_is_refused(self):
        region = Region(version=L["NOVA_TRACE_VERSION"] + 1)
        self.addCleanup(region.cleanup)
        with self.assertRaises(trace.NotFormatted):
            region.reader()

    def test_a_short_backend_is_refused(self):
        region = Region()
        self.addCleanup(region.cleanup)
        region.path.write_bytes(bytes(64))
        with self.assertRaises(trace.NotFormatted):
            trace.TraceReader(region.path, RAM_BASE, TRACE_PA)

    def test_reading_needs_no_image(self):
        """The whole point of the region header: the T layer works on a
        stripped image, and survives the S layer failing to resolve."""
        region = Region()
        self.addCleanup(region.cleanup)
        reader = region.reader()  # no ELF passed, none needed
        self.addCleanup(reader.close)
        self.assertGreater(reader.geometry.rings, 0)


class DrainTest(unittest.TestCase):
    def setUp(self):
        self.region = Region(rings=2)
        self.addCleanup(self.region.cleanup)

    def test_records_round_trip_with_their_arguments(self):
        self.region.emit(0, ts=100, code=BIND, cpu=0, a=0, b=37 | (37 << 32), c=3)
        reader = self.region.reader()
        self.addCleanup(reader.close)

        records, lost = reader.drain()
        self.assertEqual(lost, 0)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].event, "vgic.bind")
        self.assertEqual(records[0].edge, "post")
        decoded = trace.decode(records[0])
        # The firmware packs both INTIDs into one word, physical high;
        # unpacking here keeps that a detail of the EL2-to-bridge wire.
        self.assertEqual(decoded["vintid"], 37)
        self.assertEqual(decoded["pintid"], 37)
        self.assertEqual(decoded["generation"], 3)

    def test_a_second_drain_returns_only_what_is_new(self):
        self.region.emit(0, ts=1, code=TRAP)
        reader = self.region.reader()
        self.addCleanup(reader.close)
        self.assertEqual(len(reader.drain()[0]), 1)
        self.assertEqual(reader.drain()[0], [])

        self.region.emit(0, ts=2, code=TRAP)
        self.region.flush()
        records, lost = reader.drain()
        self.assertEqual([record.ts for record in records], [2])
        self.assertEqual(lost, 0)

    def test_rings_merge_by_timestamp(self):
        """CNTPCT is common to every PE, so the merge is the machine's
        real order — the thing a sampled layer cannot supply at any
        rate."""
        self.region.emit(0, ts=30, code=TRAP, cpu=0)
        self.region.emit(1, ts=10, code=TRAP, cpu=1)
        self.region.emit(0, ts=20, code=TRAP, cpu=0)
        reader = self.region.reader()
        self.addCleanup(reader.close)

        records, _ = reader.drain()
        self.assertEqual([record.ts for record in records], [10, 20, 30])
        self.assertEqual([record.cpu for record in records], [1, 0, 0])

    def test_a_lapped_writer_is_counted_not_hidden(self):
        for index in range(CAPACITY * 3):
            self.region.emit(0, ts=index, code=TRAP)
        reader = self.region.reader()
        self.addCleanup(reader.close)

        records, lost = reader.drain()
        self.assertEqual(len(records), CAPACITY)
        self.assertEqual(lost, CAPACITY * 2)
        # Every record between the cursor and the head is accounted for.
        self.assertEqual(len(records) + lost, CAPACITY * 3)
        self.assertEqual(records[0].ts, CAPACITY * 2)

    def test_a_reformatted_region_restarts_rather_than_reporting_negative_loss(self):
        """A restart that reuses the backing file rewinds head to zero.
        Subtracting a stale cursor from it would report a loss of minus
        several thousand."""
        self.region.emit(0, ts=1, code=TRAP)
        self.region.emit(0, ts=2, code=TRAP)
        reader = self.region.reader()
        self.addCleanup(reader.close)
        reader.drain()

        self.region.reformat()
        self.region.emit(0, ts=9, code=TRAP)
        self.region.flush()
        records, lost = reader.drain()
        self.assertEqual([record.ts for record in records], [9])
        self.assertGreaterEqual(lost, 0)


class SummaryTest(unittest.TestCase):
    def test_the_wire_carries_counts_and_the_last_of_each(self):
        """Bounded by the number of paths, not by the event rate: a few
        thousand a second is nothing to the bridge and a great deal to a
        browser."""
        records = [
            trace.Record(ts=1, code=TRAP, cpu=0, a=0x16, b=0, c=0),
            trace.Record(ts=2, code=TRAP, cpu=0, a=0x16, b=0, c=0),
            trace.Record(ts=3, code=BIND, cpu=0, a=0, b=37 | (37 << 32), c=4),
        ]
        summary = trace.summarise(records)
        self.assertEqual(summary["edges"], {"trap": 2, "post": 1})
        self.assertEqual(summary["last"]["post"]["pintid"], 37)
        self.assertEqual(summary["last"]["post"]["generation"], 4)

    def test_an_unknown_code_is_skipped_rather_than_invented(self):
        summary = trace.summarise([trace.Record(ts=1, code=999, cpu=0, a=0, b=0, c=0)])
        self.assertEqual(summary["edges"], {})


class CatalogueTest(unittest.TestCase):
    def test_every_event_carries_the_firmware_code(self):
        """One catalogue, two consumers. A hook the firmware numbers and
        this file does not would drain as an unknown record."""
        for event in events.EVENTS:
            with self.subTest(event=event.id):
                self.assertGreater(event.code, 0)

    def test_codes_are_unique(self):
        codes = [event.code for event in events.EVENTS]
        self.assertEqual(len(set(codes)), len(codes))

    def test_every_code_the_firmware_defines_is_catalogued(self):
        """The other direction, and the one that fails quietly: a hook
        added to the ABI header and not here writes records that arrive
        as a number nothing can name, and summarise() skips them without
        a word. The codes are read as a family, so the header is the
        only list — this checks nobody added to it alone."""
        for name, code in events._CODES.items():
            with self.subTest(code=name):
                self.assertIn(code, events.BY_CODE, f"{name} has no catalogue entry")

    def test_every_event_with_arguments_decodes_them(self):
        """An event whose record falls through decode() reaches the UI
        as a bare code and a timestamp — catalogued, and still unread."""
        for event in events.EVENTS:
            if not event.args:
                continue
            with self.subTest(event=event.id):
                decoded = trace.decode(trace.Record(ts=1, code=event.code, cpu=0, a=1, b=2, c=3))
                named = set(decoded) - {"event", "cpu", "ts"}
                self.assertTrue(named, f"{event.id} names arguments but decodes none")

    def test_a_fault_only_moment_lights_no_path(self):
        """The grade rule in paths.py: an edge may not look more certain
        than what watches it, and a hook that fires only on a fault has
        watched nothing about the working path."""
        self.assertEqual(events.BY_ID["smmu.fault"].edge, "")


if __name__ == "__main__":
    unittest.main()
