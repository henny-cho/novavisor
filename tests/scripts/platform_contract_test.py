import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services import boundaries  # noqa: E402


class PlatformBoundaryTests(unittest.TestCase):
    def test_repository_scan_targets_all_exist(self):
        # Guards every assertion below: the checker searches fixed paths,
        # so a renamed tree would empty the scan instead of failing it.
        self.assertEqual(boundaries.missing_scan_targets(REPO), [])

    def test_repository_has_no_board_reverse_dependency(self):
        self.assertEqual(boundaries.find_violations(REPO), [])

    def _complete_layout(self, root: Path):
        for tree in (*boundaries.GENERIC_TREES, boundaries.COMPONENT_TREE):
            (root / tree).mkdir(parents=True, exist_ok=True)
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
