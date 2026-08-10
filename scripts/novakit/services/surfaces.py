"""The filesystem endpoints one run is observed through.

A run's observation surfaces belong to the run, not to whichever
process is watching it: the bridge opens them for a session it serves,
and the demo runner opens them for a scenario that asks a question the
console cannot answer. Spelling the four names twice is how the second
copy goes stale.

Kept out of the workbench package for that reason. What lives there is
the reading of these surfaces; what lives here is only where they are.
"""

from __future__ import annotations

import shutil
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Surfaces:
    """Where one run's observation surfaces live.

    Whoever opened them releases them; a session resets them between
    runs so a restart never reads the previous run's RAM.
    """

    directory: Path

    @property
    def shm_path(self) -> Path:
        return self.directory / "guest-ram"

    @property
    def qmp_path(self) -> Path:
        return self.directory / "qmp.sock"

    @property
    def gdb_path(self) -> Path:
        return self.directory / "gdb.sock"

    @property
    def port_path(self) -> Path:
        """Where the bridge says which port it answers on.

        The observation surfaces are already how a second process finds
        a session — the CLI twin globs for them — and the bridge's own
        history is reachable only over its socket. A port beside the
        sockets is the same discovery answering one more question,
        rather than a second convention for finding the same session.
        """
        return self.directory / "port"

    def reset(self) -> None:
        # The port outlives a target change: it belongs to the bridge,
        # not to the machine the bridge happens to be running.
        self.shm_path.unlink(missing_ok=True)
        self.qmp_path.unlink(missing_ok=True)
        self.gdb_path.unlink(missing_ok=True)

    def release(self) -> None:
        self.reset()
        self.port_path.unlink(missing_ok=True)
        try:
            self.directory.rmdir()
        except OSError:
            pass


def sweep_stale_surfaces(base: Path, min_age_seconds: float = 60.0) -> None:
    """Remove observation directories whose bridge died without cleanup.

    A killed bridge (SIGKILL, a crashed terminal) leaves its RAM backend
    pinned in tmpfs — a gigabyte per Linux guest — until /dev/shm fills
    and every later QEMU launch fails. A directory is dead when its RAM
    file exists but nothing answers on its QMP socket; young directories
    are skipped so a bridge that is still starting is never swept.
    """
    now = time.time()
    for directory in base.glob("nova-wb-*"):
        try:
            if not (directory / "guest-ram").exists():
                continue
            if now - directory.stat().st_mtime < min_age_seconds:
                continue
            probe = directory / "qmp.sock"
            if probe.exists():
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.settimeout(0.2)
                    try:
                        sock.connect(str(probe))
                        continue  # a live QEMU still answers here
                    except OSError:
                        pass
            shutil.rmtree(directory, ignore_errors=True)
        except OSError:
            continue


def make_surfaces() -> Surfaces:
    """tmpfs keeps the RAM file's dirtied pages off the disk; fall back
    to the default temp directory where /dev/shm is unavailable."""
    base = Path("/dev/shm")
    if base.is_dir():
        sweep_stale_surfaces(base)
    root = tempfile.mkdtemp(prefix="nova-wb-", dir=base if base.is_dir() else None)
    return Surfaces(Path(root))
