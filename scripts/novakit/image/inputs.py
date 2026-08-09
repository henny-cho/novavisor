"""What a generator read, reported by the generator.

The build reruns a generator when its inputs move, and its inputs are
every file it opened: its own module, what it imported, the headers it
read. Listed by hand in a build file that is a list to forget — which is
why compilers emit depfiles instead of being told their includes.

Modules come from what the import machinery loaded; headers from the
readers in `abi`, the one door every header here is read through. A
generator that grows a dependency has nothing to remember.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..core.config import REPO

# Files read this process, in the order nobody cares about.
_READ: set[Path] = set()

_TOOLING = REPO / "scripts"


def record(path: Path) -> None:
    """Note a file that was read, for the dependency it becomes."""
    _READ.add(Path(path).resolve())


def _loaded() -> set[Path]:
    """Modules of this tooling that the import actually reached.

    From `sys.modules` rather than parsed out of the source: conditional,
    lazy and plain imports are one question, and only the loader knows.
    """
    found = set()
    for module in list(sys.modules.values()):
        name = getattr(module, "__file__", None)
        if name is None:
            continue
        path = Path(name).resolve()
        if path.is_relative_to(_TOOLING):
            found.add(path)
    return found


def depfile(output: Path) -> str:
    """A make rule naming what `output` was built from.

    Absolute paths: the reader's "here" is the build directory and this
    program's is wherever it was run from.
    """
    inputs = sorted(_loaded() | _READ)
    listed = " \\\n  ".join(_escape(path) for path in inputs)
    return f"{_escape(Path(output).resolve())}: {listed}\n"


def _escape(path: Path) -> str:
    # A space in a path would otherwise end one input and start another.
    return str(path).replace(" ", "\\ ")
