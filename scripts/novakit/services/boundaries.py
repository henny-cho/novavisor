"""Reject board-specific references and facade bypasses from reusable trees.

The same question is asked of the automation package itself: commands ->
services -> core is only a rule if something checks it, and one fact
about the tree (where the repository root is found, who reads the
workflow environment, who models the board) belongs to one module.
Which third-party packages a module may import is ruff's half of this,
declared as banned-api in ruff.toml.
"""

from __future__ import annotations

import ast
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


PACKAGE = Path("scripts/novakit")
# Lower may not import higher. core and image are both foundations: core
# owns the outside world, image owns what the build graph runs.
LAYER_DEPTH = {"core": 0, "image": 0, "services": 1, "commands": 2, "": 3}
# A fact about the tree that one module derives and the rest are handed.
# Spelling it twice is how the second copy goes stale unnoticed.
SINGLE_OWNER = {
    "parents[3]": "core/config.py",
    "GITHUB_": "core/actions.py",
    "-machine": "core/board.py",
    "__main__": "image/dtb.py, image/layout.py",
}
# Naming a marker is not using it: the table above spells all four.
RULE_SOURCE = "services/boundaries.py"


def _source_files(base: Path) -> list[Path]:
    return [path for path in sorted(base.rglob("*")) if path.suffix in SOURCE_SUFFIXES]


def _package_modules(root: Path) -> list[Path]:
    base = root / PACKAGE
    return sorted(path for path in base.rglob("*.py") if path.name != "__init__.py")


def _layer_of(path: Path, base: Path) -> str:
    parts = path.relative_to(base).parts
    return parts[0] if len(parts) > 1 else ""


def find_layer_violations(root: Path) -> list[tuple[Path, int, str, str]]:
    """Report package-relative imports that reach up, or sideways in commands."""
    base = root / PACKAGE
    violations: list[tuple[Path, int, str, str]] = []
    for path in _package_modules(root):
        here = _layer_of(path, base)
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom) or not node.level:
                continue
            # `from . import x` names a sibling, so it stays in this
            # layer; `from ..core import x` names the layer it reaches.
            head = (node.module or "").split(".")[0]
            reached = head if head and head in LAYER_DEPTH else here
            if LAYER_DEPTH[reached] > LAYER_DEPTH[here]:
                reason = f"{here or 'root'} imports the higher layer {reached}"
            elif here == "commands" and node.level == 1 and node.module is None:
                # A command reaching a command puts shared logic in the
                # top layer, where two consumers then depend on an
                # adapter instead of on a service.
                reason = "a command imports a sibling command"
            else:
                continue
            violations.append((path.relative_to(root), node.lineno, reason, "layering"))
    return violations


def find_ownership_violations(root: Path) -> list[tuple[Path, int, str, str]]:
    """Report modules restating a fact another module is the owner of."""
    base = root / PACKAGE
    violations: list[tuple[Path, int, str, str]] = []
    for path in _package_modules(root):
        name = path.relative_to(base).as_posix()
        if name == RULE_SOURCE:
            continue
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            for marker, owners in SINGLE_OWNER.items():
                if marker in line and name not in owners.split(", "):
                    violations.append(
                        (path.relative_to(root), line_number, f"{marker} belongs to {owners}", "ownership")
                    )
    return violations


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
    missing.extend(
        str(PACKAGE / layer) for layer in LAYER_DEPTH if layer and not (root / PACKAGE / layer).is_dir()
    )
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

    if (root / PACKAGE).is_dir():
        violations.extend(find_layer_violations(root))
        violations.extend(find_ownership_violations(root))
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
