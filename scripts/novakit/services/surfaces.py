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
import sys
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


def make_surfaces(need_bytes: int = 0) -> Surfaces:
    """Where one run's surfaces live.

    tmpfs first, because the RAM file's dirtied pages never reach a disk
    there — but only where it can hold the whole aperture. A /dev/shm
    too small does not fail at launch: QEMU allocates the backend
    lazily, so the machine dies when the guest touches the page that
    does not fit, with no output and no reason. Falling back to the disk
    costs writeback and keeps the run.

    Swept first, so room a dead bridge is still holding is room this run
    can have. `need_bytes` is zero for a caller that does not yet know
    which machine it will launch; the attach refuses what does not fit.
    """
    base = Path("/dev/shm")
    if base.is_dir():
        sweep_stale_surfaces(base)
        free = shutil.disk_usage(base).free
        if need_bytes and free < need_bytes:
            print(
                f"[surfaces] {base} has {free >> 20} MiB free and this run needs "
                f"{need_bytes >> 20} MiB; backing guest RAM on disk instead",
                file=sys.stderr,
            )
            base = None
    else:
        base = None
    root = tempfile.mkdtemp(prefix="nova-wb-", dir=base)
    return Surfaces(Path(root))
