#!/usr/bin/env python3
"""Reject board-specific references from reusable source trees."""

from __future__ import annotations

import argparse
from pathlib import Path

SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".h", ".hpp", ".py", ".S"}
GENERIC_TREES = (
    Path("src/nova"),
    Path("src/components"),
    Path("src/hal/arch"),
    Path("tools"),
)


def find_violations(root: Path) -> list[tuple[Path, int, str]]:
    violations: list[tuple[Path, int, str]] = []
    board_root = root / "src" / "hal" / "board"
    board_names: set[str] = set()
    if board_root.exists():
        board_names = {path.name for path in board_root.iterdir() if path.is_dir()}
    for tree in GENERIC_TREES:
        base = root / tree
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in SOURCE_SUFFIXES or path.name == Path(__file__).name:
                continue
            for line_number, line in enumerate(
                path.read_text(errors="replace").splitlines(), start=1
            ):
                if any(board_name in line for board_name in board_names):
                    violations.append(
                        (path.relative_to(root), line_number, line.strip())
                    )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()

    violations = find_violations(args.root.resolve())
    for path, line_number, line in violations:
        print(f"{path}:{line_number}: board-specific reference: {line}")
    if violations:
        print(f"platform boundary check failed: {len(violations)} violation(s)")
        return 1
    print("platform boundary check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
