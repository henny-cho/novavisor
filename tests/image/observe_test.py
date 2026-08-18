"""The observation view as a written document: what it keeps, and what
it refuses.

The document exists so the walk happens once, in the build. That only
holds if reading it back gives the same answers the walk gave, and if a
document that answers some *other* question — an older manifest, another
image, a shape this reader predates — is refused rather than believed.
The three refusals are the whole safety of skipping the walk.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from novakit.image import elfsym, observe
from tests.support import image as shared_image

ELF = shared_image.ELF

_U64 = elfsym.TypeInfo("uint", 8, name="unsigned long")
_STATE = elfsym.TypeInfo("enum", 4, name="State", enumerators=((0, "kOff"), (2, "kOn")))
_SLOT = elfsym.TypeInfo(
    "struct",
    16,
    name="Slot",
    fields=(
        elfsym.Field("deadline", 0, _U64),
        elfsym.Field("state", 8, _STATE),
        elfsym.Field("armed", 12, elfsym.TypeInfo("bool", 1)),
    ),
)


def _view() -> observe.View:
    """A view with one of every shape the encoder has a branch for."""
    return observe.View(
        {
            "timer.queue": elfsym.ResolvedSymbol(
                "nova::soft_timer::(anonymous)::g_queue",
                0x4008_0000,
                32,
                elfsym.TypeInfo("array", 32, element=_SLOT, count=2),
            ),
            "sched.cpu": elfsym.ResolvedSymbol("nova::vcpu::g_sched", 0x4008_1000, 8, _U64),
        },
        elfsym.SymbolTable({"_ZN4nova4vcpu7g_schedE": (0x4008_1000, 8)}),
        {"nova::(anonymous)::g_vttbr": elfsym.ResolvedSymbol("g_vttbr", 0x4008_2000, 8, _U64)},
        {"nova::vcpu::g_sched": 0x4008_1000},
        {observe.EC_ENUM: {0x16: "kHvcAa64", 0x24: "kDataAbortLower"}},
    )


def _same(case: unittest.TestCase, left: observe.View, right: observe.View) -> None:
    case.assertEqual(left.resolved, right.resolved)
    case.assertEqual(left.walk, right.walk)
    case.assertEqual(left.addresses, right.addresses)
    case.assertEqual(left.enums, right.enums)
    case.assertEqual(left.symbols.entries, right.symbols.entries)


class RoundTripTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.elf = Path(directory.name) / "novavisor.elf"
        self.elf.write_bytes(b"\x7fELF pretend")
        self.artifact = observe.artifact_of(self.elf)

    def write(self, view: observe.View | None = None) -> None:
        self.artifact.write_text(observe.dumps(view or _view(), self.elf))

    def test_what_comes_back_is_what_went_in(self):
        self.write()
        _same(self, _view(), observe.load(self.artifact, self.elf))

    def test_a_document_this_reader_predates_is_refused(self):
        self.write()
        document = json.loads(self.artifact.read_text())
        document["format"] = observe.FORMAT + 1
        self.artifact.write_text(json.dumps(document))
        with self.assertRaises(observe.Stale):
            observe.load(self.artifact, self.elf)

    def test_a_view_of_another_image_is_refused(self):
        """The rebuild writes the same path, so the image has to be told
        apart by what is in it."""
        self.write()
        self.elf.write_bytes(b"\x7fELF pretend, relinked")
        with self.assertRaises(observe.Stale):
            observe.load(self.artifact, self.elf)

    def test_a_document_that_is_not_one_is_refused(self):
        self.artifact.write_text("{not json")
        with self.assertRaises(observe.Stale):
            observe.load(self.artifact, self.elf)

    def test_a_document_missing_what_it_claims_to_carry_is_refused(self):
        """Past the three names, so it says it is this document. A
        fragment decoded into panels is worse than a refusal."""
        self.write()
        document = json.loads(self.artifact.read_text())
        del document["walk"]
        self.artifact.write_text(json.dumps(document))
        with self.assertRaises(observe.Stale):
            observe.load(self.artifact, self.elf)

    def test_a_view_answering_an_older_manifest_is_refused(self):
        """The quiet one. Adding a topic changes no image and moves no
        file, so every other check passes and the new panel is simply
        absent."""
        self.write()
        asked = (*observe.OBSERVED, observe.Want("sched.ghost", "nova::vcpu::g_sched"))
        with mock.patch.object(observe, "OBSERVED", asked):
            with self.assertRaises(observe.Stale):
                observe.load(self.artifact, self.elf)


@unittest.skipUnless(ELF.is_file(), "debug ELF not built")
@unittest.skipUnless(importlib.util.find_spec("elftools"), "pyelftools is not installed")
class AgainstTheImageTest(unittest.TestCase):
    """The document survives the real thing, not just a fixture."""

    def test_the_walk_and_the_document_answer_alike(self):
        view = shared_image.view()
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "novavisor.observe.json"
            artifact.write_text(observe.dumps(view, ELF))
            _same(self, view, observe.load(artifact, ELF))

    def test_the_generator_writes_a_view_and_what_it_read(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "novavisor.observe.json"
            depfile = Path(directory) / "novavisor.observe.json.d"
            code = observe.main(
                ["--elf", str(ELF), "--out", str(out), "--depfile", str(depfile)]
            )
            self.assertEqual(code, 0)
            self.assertEqual(observe.load(out, ELF).enums, shared_image.view().enums)

            # The build reruns the generator when any of these move, and
            # the manifest is the one that never relinks the image.
            rule = depfile.read_text()
            self.assertTrue(rule.startswith(f"{out}:"), rule[:120])
            for module in ("image/observe.py", "image/elfsym.py"):
                self.assertIn(module, rule)

    def test_a_manifest_naming_what_the_image_lacks_stops_the_build(self):
        """The whole reason the resolve happens in the build: a rename
        fails at the change that caused it, not in a lane later."""
        asked = (*observe.OBSERVED, observe.Want("sched.ghost", "nova::vcpu::g_no_such_global"))
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "novavisor.observe.json"
            with mock.patch.object(observe, "OBSERVED", asked):
                code = observe.main(["--elf", str(ELF), "--out", str(out)])
            self.assertEqual(code, 1)
            self.assertFalse(out.exists(), "a refused resolve left a view behind")

    def test_every_enum_the_ui_speaks_is_in_the_view(self):
        view = shared_image.view()
        for name in observe.ENUMS:
            with self.subTest(enum=name):
                self.assertGreater(len(view.enums[name]), 10)
        # Two the board leans on: the guest gate and the MMIO trap path.
        labels = view.enums[observe.EC_ENUM]
        self.assertEqual(labels[0x16], "kHvcAa64")
        self.assertEqual(labels[0x24], "kDataAbortLower")
