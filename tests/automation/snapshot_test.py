"""What a snapshotted image has to carry, and what waiting cannot fix.

A soak keeps the exact image each attempt ran so the evidence matches
the failure. An image is not only its ELF: `observe` and `walk` steps
read the observation view, which is found by name beside it. A snapshot
missing that view is an image no such step can read — and because a
stale view used to be polled like a surface that had not appeared yet,
the run reported a timeout on the topic instead of the missing file.

Both halves are checked here because either alone leaves the failure
undiagnosable: the copy keeps it from happening, the exception class
keeps it from lying about what happened.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from novakit.image import observe
from novakit.services import artifacts
from novakit.services.workbench import steps


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
