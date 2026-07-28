"""Reject board-specific references and facade bypasses from reusable trees."""

from __future__ import annotations

from pathlib import Path

SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".h", ".hpp", ".py", ".S"}
BOARD_ROOT = Path("src/hal/board")
# Programs the build graph runs are generic too: a generator that names a
# board produces one board's output. The commands above them may name
# boards freely — selecting one is what they are for.
GENERIC_TREES = (
    Path("src/nova"),
    Path("src/components"),
    Path("src/hal/arch"),
    Path("src/hal/drivers"),
    Path("scripts/novakit/image"),
)

# Components compose against hal facades, nova/*, and DEPS'd peers only.
# Reaching into a board, arch or driver tree directly ties the component
# to one platform and is what the hal/*.hpp facades exist to prevent.
COMPONENT_TREE = Path("src/components")
FORBIDDEN_COMPONENT_INCLUDES = (
    '#include "hal/board/',
    '#include "hal/arch/',
    '#include "hal/drivers/',
)


def _source_files(base: Path) -> list[Path]:
    return [path for path in sorted(base.rglob("*")) if path.suffix in SOURCE_SUFFIXES]


def _board_names(root: Path) -> set[str]:
    board_root = root / BOARD_ROOT
    if not board_root.is_dir():
        return set()
    return {
        path.name
        for path in board_root.iterdir()
        if path.is_dir() and (path / "board.cmake").is_file()
    }


def missing_scan_targets(root: Path) -> list[str]:
    """Report scan inputs that no longer exist at their hardcoded paths.

    Every rule below is a search over a fixed set of directories, so a
    directory rename does not fail the scan — it empties it. Without this
    check a moved tree reports zero violations because nothing was read.
    """
    missing = [
        str(tree)
        for tree in dict.fromkeys((*GENERIC_TREES, COMPONENT_TREE))
        if not (root / tree).is_dir()
    ]
    if not _board_names(root):
        missing.append(f"{BOARD_ROOT}/<board>/board.cmake")
    return missing


def find_violations(root: Path) -> list[tuple[Path, int, str, str]]:
    violations: list[tuple[Path, int, str, str]] = []
    board_names = _board_names(root)
    for tree in GENERIC_TREES:
        base = root / tree
        if not base.exists():
            continue
        for path in _source_files(base):
            for line_number, line in enumerate(
                path.read_text(errors="replace").splitlines(), start=1
            ):
                if any(board_name in line for board_name in board_names):
                    violations.append(
                        (
                            path.relative_to(root),
                            line_number,
                            line.strip(),
                            "board-specific reference",
                        )
                    )

    base = root / COMPONENT_TREE
    if base.exists():
        for path in _source_files(base):
            for line_number, line in enumerate(
                path.read_text(errors="replace").splitlines(), start=1
            ):
                if line.lstrip().startswith(FORBIDDEN_COMPONENT_INCLUDES):
                    violations.append(
                        (
                            path.relative_to(root),
                            line_number,
                            line.strip(),
                            "component bypasses a hal facade",
                        )
                    )
    return violations


def check(root: Path) -> int:
    """Report every scan gap and violation under root; 0 when clean."""
    missing = missing_scan_targets(root)
    for target in missing:
        print(f"missing scan target: {target}")
    if missing:
        print(f"platform boundary check failed: {len(missing)} scan target(s) missing")
        return 1

    violations = find_violations(root)
    for path, line_number, line, reason in violations:
        print(f"{path}:{line_number}: {reason}: {line}")
    if violations:
        print(f"platform boundary check failed: {len(violations)} violation(s)")
        return 1
    print("platform boundary check passed")
    return 0
