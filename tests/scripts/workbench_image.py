"""The image's answers, read once for the whole suite.

The build resolved them and wrote them beside the ELF, so this reads a
document rather than walking a debug section — milliseconds instead of
seconds, and the same answers the bridge gets.

Cached because `unittest discover` runs every module in one process and
the file does not move while they run. Nothing here opens the image: a
question this view cannot answer is a question the manifest never asked,
and asking it here would put a second, slower reader beside the one the
build already ran.
"""

from __future__ import annotations

import functools
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.image import observe  # noqa: E402

ELF = REPO / "build" / "aarch64-debug" / "novavisor.elf"
VIEW = observe.artifact_of(ELF)


@functools.cache
def view() -> observe.View:
    """Every observation, table symbol and vocabulary this image gives."""
    return observe.load(VIEW, ELF)
