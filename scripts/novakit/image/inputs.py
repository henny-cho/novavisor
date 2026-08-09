"""What a generator read, reported by the generator.

The build has to know when to run a generator again, and the answer is
every file the generator actually opened — its own module, the modules
it imported, and the headers it read for constants. Written out by hand
in the build files, that list is a list to forget: the one that reaches
the compiler is the one the compiler wrote, which is why compilers emit
depfiles instead of being told their includes.

So this reports rather than restates. Modules come from what the import
machinery loaded; headers come from the readers in `abi`, which are the
one door every header in this package is read through. A generator that
grows a dependency does not have to remember anything.
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


def read() -> set[Path]:
    """Everything recorded so far. Mostly for the tests that check it."""
    return set(_READ)


def _loaded() -> set[Path]:
    """Modules of this tooling that the import actually reached.

    Snapshotted from `sys.modules` rather than parsed out of the source:
    a conditional import, a lazy one and a plain one are all the same
    question — was it read — and only the loader knows.
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

    The format ninja and make agree on: one target, a colon, and the
    inputs. Absolute paths, because the reader's idea of "here" is the
    build directory and this program's is wherever it was run from.
    """
    inputs = sorted(_loaded() | _READ)
    listed = " \\\n  ".join(_escape(path) for path in inputs)
    return f"{_escape(Path(output).resolve())}: {listed}\n"


def _escape(path: Path) -> str:
    # A space in a path would otherwise end one input and start another.
    return str(path).replace(" ", "\\ ")
