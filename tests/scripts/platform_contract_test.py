import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services import boundaries  # noqa: E402


# The repository itself is checked by `nova test`, which calls
# boundaries.check straight after this suite and prints what it finds.
# What that run cannot exercise is the checker's own failure modes, so
# they are staged here against synthetic trees.
class PlatformBoundaryTests(unittest.TestCase):
    def _complete_layout(self, root: Path):
        for tree in (*boundaries.GENERIC_TREES, boundaries.COMPONENT_TREE):
            (root / tree).mkdir(parents=True, exist_ok=True)
        for layer in boundaries.LAYER_DEPTH:
            if layer:
                (root / boundaries.PACKAGE / layer).mkdir(parents=True, exist_ok=True)
        board = root / boundaries.BOARD_ROOT / "sample_board"
        board.mkdir(parents=True)
        (board / "board.cmake").write_text("")

    def test_renamed_tree_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._complete_layout(root)
            self.assertEqual(boundaries.missing_scan_targets(root), [])

            moved = root / boundaries.COMPONENT_TREE
            moved.rename(moved.with_name("modules"))

            self.assertIn(
                str(boundaries.COMPONENT_TREE), boundaries.missing_scan_targets(root)
            )

    def test_board_root_without_boards_is_reported(self):
        # An empty board set makes the board-name scan compare every line
        # against nothing, which no source can ever fail.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._complete_layout(root)
            (root / boundaries.BOARD_ROOT / "sample_board" / "board.cmake").unlink()

            self.assertEqual(
                boundaries.missing_scan_targets(root),
                [f"{boundaries.BOARD_ROOT}/<board>/board.cmake"],
            )

    def test_board_reference_in_generic_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            board = root / "src" / "hal" / "board" / "qemu_virt"
            board.mkdir(parents=True)
            (board / "board.cmake").write_text("")
            source = root / "src" / "nova" / "sample.hpp"
            source.parent.mkdir(parents=True)
            source.write_text("constexpr int kBase = qemu_virt::kBase;\n")

            violations = boundaries.find_violations(root)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0][:2], (source.relative_to(root), 1))
        self.assertEqual(violations[0][3], "board-specific reference")

    def test_component_bypassing_a_hal_facade_is_rejected(self):
        # Components may include hal/*.hpp facades, nova/*, and DEPS'd
        # peers — never a board, arch or driver tree directly.
        for include in ('#include "hal/board/active/board.hpp"',
                        '#include "hal/arch/aarch64/cpu.hpp"',
                        '#include "hal/drivers/pl011.hpp"'):
            with self.subTest(include=include), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "src" / "components" / "sample" / "sample.cpp"
                source.parent.mkdir(parents=True)
                source.write_text(f"{include}\n")

                violations = boundaries.find_violations(root)

                self.assertEqual(len(violations), 1)
                self.assertEqual(violations[0][:2], (source.relative_to(root), 1))
                self.assertEqual(violations[0][3], "component bypasses a hal facade")

    def test_hal_facade_may_include_a_board_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src" / "hal" / "console.hpp"
            source.parent.mkdir(parents=True)
            source.write_text('#include "hal/board/active/uart.hpp"\n')

            self.assertEqual(boundaries.find_violations(root), [])


class AutomationLayerTests(unittest.TestCase):
    """The same question asked of the automation package itself.

    Which third-party packages a module may import is ruff's half of
    this, declared as banned-api in ruff.toml. What is left here is the
    shape of the package: who may import whom, and which module owns a
    fact the others are handed.
    """

    def _package(self, root: Path) -> Path:
        package = root / boundaries.PACKAGE
        for layer in boundaries.LAYER_DEPTH:
            if layer:
                (package / layer).mkdir(parents=True)
        return package

    def test_an_upward_import_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = self._package(root) / "core" / "reaching.py"
            module.write_text("from ..services import cmake\n")

            violations = boundaries.find_layer_violations(root)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0][0], module.relative_to(root))
        self.assertIn("higher layer services", violations[0][2])

    def test_a_command_importing_a_command_is_rejected(self):
        # Shared logic between two commands belongs to a service; reaching
        # sideways leaves it in the top layer with two consumers. Both
        # spellings of a sibling import say the same thing.
        for spelling in ("from . import two", "from .two import value"):
            with self.subTest(spelling=spelling), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                commands = self._package(root) / "commands"
                (commands / "one.py").write_text(f"{spelling}\n")
                (commands / "two.py").write_text("value = 1\n")

                violations = boundaries.find_layer_violations(root)

                self.assertEqual(len(violations), 1)
                self.assertIn("sibling command", violations[0][2])

    def test_a_command_reaching_past_services_is_rejected(self):
        # A command adapts one service call to the CLI. Reaching a
        # foundation directly puts that knowledge in the top layer, and
        # depth alone would allow it — core sits below services.
        for spelling in ("from ..core import proc", "from ..image import dtb"):
            with self.subTest(spelling=spelling), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (self._package(root) / "commands" / "one.py").write_text(f"{spelling}\n")

                violations = boundaries.find_layer_violations(root)

                self.assertEqual(len(violations), 1)
                self.assertIn("past services", violations[0][2])

    def test_a_nested_package_may_reach_its_own_layer(self):
        # `from .. import x` inside services/workbench names services,
        # not the root: a nested package spends one dot on itself, so
        # counting dots would read this as leaving the layer.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = self._package(root)
            nested = package / "services" / "workbench"
            nested.mkdir()
            (nested / "one.py").write_text("from .. import cmake\nfrom ...core import board\n")
            (package / "services" / "cmake.py").write_text("value = 1\n")

            self.assertEqual(boundaries.find_layer_violations(root), [])

    def test_a_downward_import_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = self._package(root)
            (package / "commands" / "one.py").write_text("from ..services import cmake\n")
            (package / "services" / "cmake.py").write_text("from ..core import proc\n")

            self.assertEqual(boundaries.find_layer_violations(root), [])

    def test_a_second_copy_of_an_owned_fact_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = self._package(root) / "services" / "rogue.py"
            module.write_text("summary = 'GITHUB_STEP_SUMMARY'\n")

            violations = boundaries.find_ownership_violations(root)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0][0], module.relative_to(root))
        self.assertIn("core/actions.py", violations[0][2])

    def test_the_owner_itself_is_not_a_violation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = self._package(root) / "core" / "actions.py"
            module.write_text("summary = 'GITHUB_STEP_SUMMARY'\n")

            self.assertEqual(boundaries.find_ownership_violations(root), [])

    def test_a_renamed_layer_is_reported(self):
        # Every rule above searches fixed paths, so a renamed layer would
        # empty the scan instead of failing it.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = self._package(root)
            (package / "services").rename(package / "helpers")

            self.assertIn(
                str(boundaries.PACKAGE / "services"), boundaries.missing_scan_targets(root)
            )


class PlatformContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.build = self.root / "build"
        self.arch = self.source / "arch" / "test_arch"
        self.board = self.source / "board" / "test_board"
        self.project = self.source / "project" / "test_project"
        for directory in (self.arch, self.board, self.project):
            directory.mkdir(parents=True)

        (self.arch / "CMakeLists.txt").write_text(
            "add_library(nova_arch INTERFACE)\n"
            "add_custom_target(nova_arch_linker_script)\n"
        )
        (self.board / "CMakeLists.txt").write_text(
            "add_library(nova_board INTERFACE)\n"
        )
        (self.board / "board.cmake").write_text(
            'set(NOVA_BOARD_ARCH "test_arch")\n'
            'set(NOVA_BOARD_REQUIRED_CPU "test_cpu")\n'
            "set(NOVA_BOARD_CAPABILITIES gicv3 smmuv3 dma)\n"
        )
        (self.board / "device_inventory.yml").write_text("devices: []\n")
        facade = self.board / "include" / "hal" / "board" / "active"
        facade.mkdir(parents=True)
        for name in (
            "board.hpp",
            "board_layout.h",
            "gicv3.hpp",
            "uart.hpp",
            "smmuv3.hpp",
            "dma_device.hpp",
        ):
            (facade / name).write_text("#pragma once\n")
        (self.project / "CMakeLists.txt").write_text("")
        self.write_project_manifest("gicv3;smmuv3;dma", "core_gic;vgic;smmu;dma_device;dma_probe")
        self.write_root()

    def tearDown(self):
        self.temp.cleanup()

    def write_project_manifest(self, capabilities: str, components: str):
        (self.project / "project.cmake").write_text(
            'set(NOVA_PROJECT_ARCH "test_arch")\n'
            'set(NOVA_PROJECT_BOARD "test_board")\n'
            f'set(NOVA_PROJECT_CAPABILITIES "{capabilities}")\n'
            f'set(NOVA_COMPONENTS "{components}")\n'
        )

    def write_root(self, arch: str = "test_arch", board: str = "test_board", project: str = "test_project"):
        (self.source / "CMakeLists.txt").write_text(
            textwrap.dedent(
                f"""
                cmake_minimum_required(VERSION 3.25)
                project(platform_contract NONE)
                include("{REPO / 'cmake' / 'nova_platform_contract.cmake'}")
                set(NOVA_ARCH "{arch}")
                set(NOVA_BOARD "{board}")
                set(NOVA_PROJECT "{project}")
                set(NOVA_ARCH_DIR "${{CMAKE_SOURCE_DIR}}/arch/${{NOVA_ARCH}}")
                set(NOVA_BOARD_DIR "${{CMAKE_SOURCE_DIR}}/board/${{NOVA_BOARD}}")
                set(NOVA_PROJECT_DIR "${{CMAKE_SOURCE_DIR}}/project/${{NOVA_PROJECT}}")
                set(NOVA_BOARD_INCLUDE_DIR "${{NOVA_BOARD_DIR}}/include")
                nova_validate_selection_paths()
                include("${{NOVA_BOARD_DIR}}/board.cmake")
                include("${{NOVA_PROJECT_DIR}}/project.cmake")
                nova_validate_platform_manifest()
                add_subdirectory("${{NOVA_BOARD_DIR}}" board-build)
                add_subdirectory("${{NOVA_ARCH_DIR}}" arch-build)
                set(NOVA_LINKER_SCRIPT "${{CMAKE_BINARY_DIR}}/linker.ld")
                nova_validate_platform_targets()
                """
            )
        )

    def configure(self, expected: str | None = None):
        result = subprocess.run(
            ["cmake", "-S", str(self.source), "-B", str(self.build)],
            capture_output=True,
            text=True,
        )
        output = result.stdout + result.stderr
        if expected is None:
            self.assertEqual(result.returncode, 0, output)
        else:
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn(expected, output)

    def test_full_capability_contract_is_valid(self):
        self.configure()

    def test_project_without_smmu_or_dma_is_valid(self):
        self.write_project_manifest("gicv3", "core_gic;vgic")
        self.configure()

    def test_invalid_arch_board_and_project_are_rejected(self):
        for selection, expected in (
            (("missing", "test_board", "test_project"), "Unsupported NOVA_ARCH"),
            (("test_arch", "missing", "test_project"), "Unsupported NOVA_BOARD"),
            (("test_arch", "test_board", "missing"), "Unsupported NOVA_PROJECT"),
        ):
            with self.subTest(selection=selection):
                self.write_root(*selection)
                self.configure(expected)
                if self.build.exists():
                    subprocess.run(["cmake", "-E", "remove_directory", str(self.build)])

    def test_missing_capability_component_is_rejected(self):
        self.write_project_manifest("gicv3;smmuv3;dma", "core_gic;vgic;smmu;dma_device")
        self.configure("requires component 'dma_probe'")

    def test_missing_active_facade_is_rejected(self):
        (self.board / "include" / "hal" / "board" / "active" / "gicv3.hpp").unlink()
        self.configure("missing active facade 'gicv3.hpp'")

    def test_missing_platform_target_is_rejected(self):
        (self.board / "CMakeLists.txt").write_text("")
        self.configure("missing target 'nova_board'")


if __name__ == "__main__":
    unittest.main()
