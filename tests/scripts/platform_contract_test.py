import importlib.util
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
CHECKER_PATH = REPO / "tools" / "check_platform_boundaries.py"
SPEC = importlib.util.spec_from_file_location("platform_boundaries", CHECKER_PATH)
assert SPEC and SPEC.loader
BOUNDARIES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOUNDARIES)


class PlatformBoundaryTests(unittest.TestCase):
    def test_repository_has_no_board_reverse_dependency(self):
        self.assertEqual(BOUNDARIES.find_violations(REPO), [])

    def test_board_reference_in_generic_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src" / "hal" / "board" / "qemu_virt").mkdir(parents=True)
            source = root / "src" / "components" / "sample" / "sample.cpp"
            source.parent.mkdir(parents=True)
            source.write_text('#include "hal/board/qemu_virt/board.hpp"\n')

            violations = BOUNDARIES.find_violations(root)

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0][:2], (source.relative_to(root), 1))


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
                set(NOVA_BOARD_CPU "test_cpu")
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

    def test_cpu_mismatch_is_rejected(self):
        board_manifest = self.board / "board.cmake"
        board_manifest.write_text(
            board_manifest.read_text().replace("test_cpu", "different_cpu")
        )
        self.configure("requires CPU model")

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
