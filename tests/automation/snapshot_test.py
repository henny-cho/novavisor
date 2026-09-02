"""What an image has to carry when it is copied, and what waiting cannot fix.

An image is not only its ELF: `observe` and `walk` steps read it through
the observation view beside it. A copy that leaves the view behind is an
image no such step can read — and because a stale view used to be polled
like a surface that had not appeared yet, the run reported a timeout on
the topic instead of the missing file.

Three places copy an image: a soak snapshot and two evidence bundles.
They share `artifacts.copy_image` so the answer cannot differ between
them, and the tests below check the rule once and then check that each
caller actually reaches it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from novakit.image import observe
from novakit.services import artifacts, report
from novakit.services.workbench import steps


class CopyImageTest(unittest.TestCase):
    """The one rule the three copy sites share."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="nova-img-")))
        self.elf = self.root / "novavisor.elf"
        self.elf.write_bytes(b"an image")

    def test_the_view_travels_with_the_elf(self):
        observe.artifact_of(self.elf).write_text("{}")
        into = self.root / "kept" / "novavisor.elf"
        copied = artifacts.copy_image(self.elf, into)
        self.assertEqual(copied, (into, observe.artifact_of(into)))
        self.assertTrue(all(path.is_file() for path in copied))

    def test_a_rename_carries_the_view_to_the_matching_name(self):
        """Only observe.artifact_of names a view, at both ends."""
        observe.artifact_of(self.elf).write_text("{}")
        into = self.root / "kept" / "variant-1-novavisor.elf"
        copied = artifacts.copy_image(self.elf, into)
        self.assertEqual(copied[1].name, "variant-1-novavisor.observe.json")

    def test_a_source_without_a_view_copies_the_elf_alone(self):
        """No invented view: the reader reports the real absence by name."""
        into = self.root / "kept" / "novavisor.elf"
        self.assertEqual(artifacts.copy_image(self.elf, into), (into,))
        self.assertFalse(observe.artifact_of(into).exists())
        with self.assertRaises(observe.Stale):
            observe.view_of(into)


class EvidenceTest(unittest.TestCase):
    """A failure bundle holds images the same way a snapshot does."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="nova-ev-")))
        self.paths = report.ArtifactPaths(self.root)
        self.paths.initialize()

    def image(self, at: Path) -> Path:
        at.parent.mkdir(parents=True, exist_ok=True)
        at.write_bytes(b"an image")
        observe.artifact_of(at).write_text("{}")
        return at

    def test_a_kept_image_registers_both_files_for_checksumming(self):
        """Or the bundle's sha256sums would not cover what it copied."""
        evidence = report.Evidence(self.paths)
        evidence.keep_image(self.image(self.root / "src" / "novavisor.elf"), "variant-1-novavisor.elf")
        self.assertEqual(
            sorted(path.name for path in evidence.collected),
            ["variant-1-novavisor.elf", "variant-1-novavisor.observe.json"],
        )
        evidence.finish()
        sums = (evidence.root / "sha256sums.txt").read_text()
        self.assertIn("variant-1-novavisor.observe.json", sums)

    def test_an_image_that_is_not_there_is_skipped_not_raised(self):
        evidence = report.Evidence(self.paths)
        evidence.keep_image(self.root / "absent.elf")
        self.assertEqual(evidence.collected, [])

    def test_a_failed_demo_bundle_carries_the_view_of_every_variant(self):
        """The bundle is opened after the run is gone; it must still answer."""
        snapshots = [self.image(self.root / "snap" / f"v{index}" / "novavisor.elf")
                     for index in (1, 2)]
        with mock.patch.object(report.config, "BUILD_ROOT", self.root / "build"):
            report.collect_evidence(self.paths, "10_console_mux", {}, snapshots)
        kept = {path.name for path in (self.root / "evidence").iterdir()}
        for index in (1, 2):
            self.assertIn(f"variant-{index}-novavisor.elf", kept)
            self.assertIn(f"variant-{index}-novavisor.observe.json", kept)

    def test_a_kept_preset_carries_the_view_too(self):
        with mock.patch.object(report.config, "BUILD_ROOT", self.root / "build"):
            self.image(self.root / "build" / "p" / "novavisor.elf")
            evidence = report.Evidence(self.paths)
            evidence.keep_preset("p")
        self.assertIn("p-novavisor.observe.json", [path.name for path in evidence.collected])


class SnapshotTest(unittest.TestCase):
    """A snapshot is the image and how the image answers questions."""

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="nova-snap-")))
        self.built = self.root / "build" / "novavisor.elf"
        self.built.parent.mkdir(parents=True)
        self.built.write_bytes(b"an image")
        self.snapshot = self.root / "keep" / "novavisor.elf"

    def take(self) -> Path:
        """Snapshot the built image the way scenario_for does."""
        with (
            mock.patch.object(artifacts, "build_demos", return_value=self.root),
            mock.patch.object(artifacts, "prepare_payload_manifest", return_value=None),
            mock.patch.object(artifacts.cmake, "build", return_value=self.built),
            mock.patch.object(artifacts, "build_qemu_cmd", return_value=["qemu"]),
            mock.patch.object(artifacts.manifests, "manifest_pattern_list", return_value=()),
            mock.patch.object(artifacts.manifests, "variant_preset", return_value="p"),
        ):
            scenario = artifacts.scenario_for(
                "demo", {"timeout_seconds": 1}, {}, elf_snapshot=self.snapshot
            )
        return Path(scenario.elf)

    def test_the_view_travels_with_the_image_it_describes(self):
        observe.artifact_of(self.built).write_text("{}")
        taken = self.take()
        self.assertEqual(taken, self.snapshot)
        self.assertTrue(taken.is_file())
        self.assertTrue(
            observe.artifact_of(taken).is_file(),
            "the snapshot carries no view, so no observe or walk step can read it",
        )

    def test_the_snapshot_reaches_the_shared_copy_rule(self):
        """A second copy policy here would drift from the other two."""
        with mock.patch.object(artifacts, "copy_image", wraps=artifacts.copy_image) as copied:
            self.take()
        copied.assert_called_once_with(self.built, self.snapshot)

    def test_a_build_with_no_view_still_snapshots_the_image(self):
        """The refusal belongs to the reader, which names the file."""
        self.assertFalse(observe.artifact_of(self.built).exists())
        taken = self.take()
        self.assertTrue(taken.is_file())
        with self.assertRaises(observe.Stale):
            observe.view_of(taken)


class WaitingTest(unittest.TestCase):
    """Which absences a step waits through, and which it reports at once."""

    def test_a_stale_view_is_not_something_to_wait_for(self):
        """Every Stale says "rebuild", so no amount of polling makes it true."""
        self.assertNotIn(observe.Stale, steps.NOT_YET)

    def test_a_surface_the_run_has_not_placed_yet_is(self):
        self.assertIn(FileNotFoundError, steps.NOT_YET)


if __name__ == "__main__":
    unittest.main()
