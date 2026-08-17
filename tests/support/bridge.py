"""A bridge as a run would have handed it over.

Two shapes are wanted. `bridge()` is the plain one: a socket-side object
with no machine behind it, which is what every test of an answer, a
refusal or a replay needs. `running()` is a bridge mid-run — the state a
launch leaves behind, which is five separate assignments nobody should
have to rediscover to test the layer above them.

Knobs rather than a base class: a test states the one thing it is about
and inherits nothing it then has to read past. Counterparts stay with the
tests that use them; what belongs here is assembly, not stand-ins. A
provider or a symbol table faked here would be produced and consumed by
the same code, and a round trip through one encoder proves nothing.
"""

from __future__ import annotations

from pathlib import Path

from novakit.services.surfaces import Surfaces
from novakit.services.workbench.server import Bridge
from novakit.services.workbench.session import Phase

# A UI root for the socket-side tests: they never serve a file, and a
# real directory would suggest they might.
NOWHERE = Path("/nonexistent")


def bridge(*, ui_root: Path = NOWHERE, surfaces: Surfaces | None = None,
           trace_history: int | None = None) -> Bridge:
    """A bridge with nothing behind it, drained of its opening frames."""
    made = Bridge(
        ui_root=ui_root,
        **({} if surfaces is None else {"surfaces": surfaces}),
        **({} if trace_history is None else {"trace_history": trace_history}),
    )
    made.store.drain()
    return made


def running(directory: Path, *, run_id: int = 1, board: dict[str, int] | None = None,
            shm: bytes | None = None, view: object | None = None,
            ui_root: Path | None = None) -> Bridge:
    """A bridge whose session is mid-run, backed by `directory`.

    `shm` places the RAM file the observation layers mmap; `view` stands
    for the image the build resolved, which the S layer refuses to poll
    without. `board` replaces the header numbers when a test puts its
    region somewhere it can write to.
    """
    surfaces = Surfaces(directory)
    if shm is not None:
        surfaces.shm_path.write_bytes(shm)
    made = bridge(ui_root=directory if ui_root is None else ui_root, surfaces=surfaces)
    made.session.phase = Phase.RUNNING
    made.session.elf_path = directory / "novavisor.elf"
    made.session.run_id = run_id
    made.session.view = view
    if board is not None:
        made._board = board
    made.store.drain()
    return made
