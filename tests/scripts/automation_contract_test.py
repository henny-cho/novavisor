"""Public automation contracts, asked of the command registry itself."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from typer.main import get_command
from typer.testing import CliRunner

REPO = Path(__file__).resolve().parents[2]
NOVA = REPO / "scripts" / "nova"
PRESETS = REPO / "CMakePresets.json"
sys.path.insert(0, str(REPO / "scripts"))

from novakit import cli  # noqa: E402
from novakit.core import board, config  # noqa: E402
from novakit.services import ci, gates, tfa  # noqa: E402

RUNNER = CliRunner()


def leaves(command, path: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Every runnable command in the app tree, by the words that reach it."""
    children = getattr(command, "commands", {})
    if not children:
        return [path] if path else []
    return [leaf for name, child in children.items() for leaf in leaves(child, (*path, name))]


class PublicCommandContractTest(unittest.TestCase):
    def test_every_registered_command_answers_for_itself(self):
        # Walking the registry covers whatever is registered, so a new
        # command is in scope the moment it exists.
        found = leaves(get_command(cli.app))
        self.assertTrue(found, "the command tree walk found nothing to check")
        for command in found:
            with self.subTest(command=command):
                result = RUNNER.invoke(cli.app, [*command, "--help"], color=False)
                self.assertEqual(result.exit_code, 0, result.output)

    def test_demo_set_operations_require_one_scope(self):
        # A set operation acts on one demo or on all of them; leaving it
        # unsaid, or saying both, is a usage error rather than a default.
        for operation in ("fetch", "verify"):
            with self.subTest(operation=operation, scope="missing"):
                self.assertEqual(RUNNER.invoke(cli.app, ["demo", operation]).exit_code, 2)
            with self.subTest(operation=operation, scope="conflicting"):
                self.assertEqual(RUNNER.invoke(cli.app, ["demo", operation, "1", "--all"]).exit_code, 2)

    def test_workbench_attachment_leaves_the_board_model_frozen(self):
        base = board.command(kernel=Path("novavisor.elf"))
        # The list handed over is the one checked afterwards. Passing a
        # throwaway copy asked whether `command()` is deterministic, and
        # a rewrite of the caller's argument went unnoticed.
        given = list(base)
        attached = board.attach_workbench(
            given,
            shm_path="/dev/shm/wb.ram",
            qmp_path="/tmp/wb.qmp",
            gdb_path="/tmp/wb.gdb",
        )

        machine = attached[attached.index("-machine") + 1]
        self.assertTrue(machine.endswith(",memory-backend=wbram"))
        self.assertIn(
            "memory-backend-file,id=wbram,size=1024M,mem-path=/dev/shm/wb.ram,share=on",
            attached,
        )
        self.assertIn("unix:/tmp/wb.qmp,server=on,wait=off", attached)
        self.assertIn("unix:/tmp/wb.gdb,server=on,wait=off", attached)
        # The observation surfaces are additive: the list that went in,
        # the model it was built from and the frozen tuple are all left
        # as they were.
        self.assertEqual(given, base)
        self.assertEqual(base, board.command(kernel=Path("novavisor.elf")))
        self.assertNotIn("memory-backend", " ".join(board.MACHINE_ARGS))


class BuildPresetContractTest(unittest.TestCase):
    def test_every_preset_the_automation_names_is_defined(self):
        # The names come from the modules that build them, so this asks
        # whether Python and CMake agree — not whether a list was updated.
        required = {
            config.HV_PRESET,
            gates.HOST_PRESET,
            ci.RECHECK_PRESET,
            *ci.RUNTIME_PRESETS,
            *ci.EVIDENCE_PRESETS,
            *(profile.preset for profile in tfa.PROFILES.values()),
        }
        data = json.loads(PRESETS.read_text())
        self.assertLessEqual(required, {preset["name"] for preset in data["configurePresets"]})
        self.assertLessEqual(required, {preset["name"] for preset in data["buildPresets"]})

    def test_public_entrypoints_are_executable(self):
        self.assertTrue(NOVA.stat().st_mode & 0o111)


class GeneratedInputContractTest(unittest.TestCase):
    """A build step that produces something has to declare its inputs.

    Every other step beside the link is a check, and a check can be a
    POST_BUILD command: it declares nothing, runs whenever the target
    relinks, and leaves nothing behind to go stale. A generator cannot.
    Its second input — the manifest, the configuration — moves without
    relinking anything, and a POST_BUILD step would not notice.

    Both generators in this build report their own inputs, so the check
    is that neither is written the other way.
    """

    GUEST_PROJECT = (REPO / "cmake" / "nova_guest_project.cmake").read_text()

    def test_each_generator_declares_a_depfile(self):
        generated = [
            block
            for block in self.GUEST_PROJECT.split("add_custom_command(")[1:]
            if "OUTPUT" in block.split(")")[0] or "OUTPUT " in block[:40]
        ]
        self.assertGreaterEqual(len(generated), 2, "expected the guest bundle and the view")
        for block in generated:
            body = block[: block.index("COMMENT")]
            with self.subTest(output=body.split("\n")[0].strip()):
                self.assertIn("DEPFILE", body)

    def test_no_generator_hides_behind_a_post_build_step(self):
        for block in self.GUEST_PROJECT.split("add_custom_command(TARGET")[1:]:
            body = block[: block.index("COMMENT")]
            with self.subTest(step=body.split("COMMAND")[1].strip().split("\n")[0]):
                self.assertNotIn("--out", body, "a POST_BUILD step cannot declare its inputs")


if __name__ == "__main__":
    unittest.main()
