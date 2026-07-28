"""The automation package's layering, enforced instead of documented.

commands -> services -> core is only a rule if something checks it. Without
this test the first convenient upward import turns the layers back into one
flat namespace, and the cycle it creates only shows up as an ImportError in
whatever imports last.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "scripts" / "novakit"
sys.path.insert(0, str(REPO / "scripts"))

# Lower may not import higher. core and image are both foundations: core
# owns the outside world, image owns what the build graph runs.
DEPTH = {"core": 0, "image": 0, "services": 1, "commands": 2, "": 3}


def modules() -> list[Path]:
    return sorted(path for path in PACKAGE.rglob("*.py") if path.name != "__init__.py")


def layer_of(path: Path) -> str:
    parts = path.relative_to(PACKAGE).parts
    return parts[0] if len(parts) > 1 else ""


def imported_layers(path: Path) -> set[str]:
    """Which layers this module reaches through a package-relative import."""
    here = layer_of(path)
    reached = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        # `from . import x` names a sibling, so it stays in this layer;
        # `from ..core import x` names the layer it reaches into.
        head = (node.module or "").split(".")[0]
        reached.add(head if head and head in DEPTH else here)
    return reached


def modules_importing(name: str) -> list[str]:
    """Modules that import the named top-level package."""
    importers = []
    for path in modules():
        for node in ast.walk(ast.parse(path.read_text())):
            imported = (
                any(alias.name.split(".")[0] == name for alias in node.names)
                if isinstance(node, ast.Import)
                else isinstance(node, ast.ImportFrom)
                and not node.level
                and (node.module or "").split(".")[0] == name
            )
            if imported:
                importers.append(path.relative_to(PACKAGE).as_posix())
                break
    return importers


class LayerTests(unittest.TestCase):
    def test_every_layer_exists(self):
        # A renamed layer would empty this scan instead of failing it.
        for layer in DEPTH:
            if layer:
                self.assertTrue((PACKAGE / layer).is_dir(), layer)

    def test_no_module_imports_a_higher_layer(self):
        for path in modules():
            here = layer_of(path)
            for reached in imported_layers(path):
                with self.subTest(module=path.relative_to(PACKAGE), reached=reached):
                    self.assertLessEqual(DEPTH[reached], DEPTH[here])

    def test_only_spawn_knows_about_pexpect(self):
        # The verification state machine stays testable against a fake child.
        self.assertEqual(modules_importing("pexpect"), ["services/spawn.py"])

    def test_only_proc_spawns_subprocesses(self):
        # image/ programs are standalone: the build graph runs them directly,
        # so they may not reach the CLI's process boundary.
        self.assertEqual(
            modules_importing("subprocess"),
            ["core/proc.py", "image/dtb.py", "image/layout.py"],
        )

    def test_only_core_actions_reads_the_workflow_environment(self):
        # Three modules used to ask the environment whether this was CI, and
        # two of them owned a copy of the step-summary append.
        owners = [
            path.relative_to(PACKAGE).as_posix()
            for path in modules()
            if "GITHUB_" in path.read_text()
        ]
        self.assertEqual(owners, ["core/actions.py"])

    def test_the_board_model_has_one_owner(self):
        owners = [
            path.relative_to(PACKAGE).as_posix()
            for path in modules()
            if "-machine" in path.read_text()
        ]
        self.assertEqual(owners, ["core/board.py"])


if __name__ == "__main__":
    unittest.main()
