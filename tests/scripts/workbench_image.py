"""The debug image, parsed once for the whole suite.

Reading the DWARF is seconds of pure Python per parse, and the answer
cannot change while the tests run — the image is a file on disk that
nothing here writes. So the parse is shared: `unittest discover` runs
every test module in one process, which is what lets a cache in this
module reach across the files that import it.

Both handles are held for the life of the process on purpose. The index
keeps the ELF open and memoises the DIEs it has walked, so a caller must
not close it; closing would leave the next caller reading a closed file
and pay for the walk again.
"""

from __future__ import annotations

import atexit
import functools
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import elfsym, snapshot  # noqa: E402

ELF = REPO / "build" / "aarch64-debug" / "novavisor.elf"


@functools.cache
def view() -> snapshot.ImageView:
    """Every observation and table symbol resolved against the image."""
    return snapshot.resolve_image(ELF)


@functools.cache
def index() -> elfsym.ElfIndex:
    """The image's symbols and DWARF, for what the view does not carry.

    Released at exit rather than by a caller: the handle is shared, and
    whoever finished with it first would close it under the rest.
    """
    made = elfsym.ElfIndex(ELF)
    atexit.register(made.close)
    return made
