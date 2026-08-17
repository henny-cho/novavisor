"""Publish S-layer snapshots and maintain observation/memory-map state."""

from __future__ import annotations

import asyncio  # noqa: TID251 — the event loop lives here
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ...image import observe
from . import commands, observations, regimes, snapshot
from .protocol import Kind, Src, Topic
from .session import Phase

if TYPE_CHECKING:
    from .session import Session
    from .store import StateStore

POLL_INTERVAL_SECONDS = 0.05


class ObservationPoller:
    """Manages DWARF observation providers, memory maps, and S-layer polling."""

    def __init__(
        self,
        store: StateStore,
        session: Session,
        board_numbers_fn: Callable[[], dict[str, int]],
        reject_fn: Callable[[str, str | None], None],
    ) -> None:
        self.store = store
        self.session = session
        self._board_numbers = board_numbers_fn
        self._reject = reject_fn

        self.poller: snapshot.SnapshotPoller | None = None
        self.provider: snapshot.SnapshotProvider | None = None
        self.provider_run: int | None = None
        self.provider_failed: int | None = None
        self.capture: regimes.Capture | None = None
        self.writer: commands.Writer | None = None
        self.writer_run: int | None = None

    def drop_provider(self) -> None:
        provider, self.provider = self.provider, None
        writer, self.writer = self.writer, None
        self.poller = None
        self.provider_run = None
        self.capture = None
        self.writer_run = None
        if provider is not None:
            provider.close()
        if writer is not None:
            writer.close()

    def refresh_memory_map(self) -> None:
        """Publish this run's topology whenever it has moved."""
        if self.capture is None:
            return
        topology = self.capture.refresh()
        if topology is not None:
            self.session.adopt_memory_map(topology)

    def ensure_writer(self) -> commands.Writer | None:
        """This run's write window, opened once the page exists.

        Where the page is, is the image's answer and not the board's: the
        firmware allocates it as an ordinary global, so the symbol table
        is the only thing that knows the address. The demo step writes
        through the same symbol, which is what keeps one page under both.

        Opening the window is also what tells a reader the run takes
        commands, so the rows go out here: the panel is built from them,
        and a window opened without publishing them is a machine that
        accepts commands nobody is offered.
        """
        session = self.session
        if session.phase is not Phase.RUNNING or session.surfaces is None:
            return None
        current = session.run_id
        if self.writer_run == current:
            return self.writer
        if self.writer is not None:
            self.writer.close()
            self.writer = None
        shm_path = session.surfaces.shm_path
        if not shm_path.exists() or shm_path.stat().st_size == 0:
            return None
        symbols = snapshot.image_symbols(self.provider)
        if symbols is None:
            return None  # the S layer has not read the image yet
        if not symbols.has(observations.COMMAND_PAGE):
            # This build carries no command component. Settled for the
            # run: the absent block is how a reader is told so.
            self.writer_run = current
            return None
        page, size = symbols.extent_of(observations.COMMAND_PAGE)
        try:
            self.writer = commands.Writer(
                shm_path, self._board_numbers()["NOVA_BOARD_PHYS_RAM_BASE"], page, size
            )
        except commands.NotYetFormatted:
            # EL2 has not placed the page yet. Nothing is settled, so no
            # reason is given and the next tick looks again.
            return None
        except commands.NotFormatted as error:
            # A layout this build cannot read will not become readable by
            # asking again: settle the run, and say why once.
            self.writer_run = current
            self._reject(f"cmd: {error}", None)
            return None
        except (FileNotFoundError, OSError):
            return None
        self.writer_run = current
        self.session.adopt_command_ring(self.writer.as_dict())
        return self.writer

    def _build_provider(self, elf_path: Path, shm_path: Path):
        """This run's S reader: RAM mapped here, the image already answered by the build."""
        view = self.session.view
        if view is None:
            raise observe.Stale(f"no observation view for {elf_path.name}")
        return snapshot.open_provider(
            elf_path, shm_path, self._board_numbers()["NOVA_BOARD_PHYS_RAM_BASE"], view
        )

    async def ensure_poller(self) -> snapshot.SnapshotPoller | None:
        """The S reader for this run, built once and shared."""
        session = self.session
        if session.phase is not Phase.RUNNING or session.elf_path is None:
            return None
        if session.surfaces is None:
            return None
        current = session.run_id
        if self.provider_run == current:
            return self.poller
        if self.provider_failed == current:
            return None
        self.drop_provider()
        shm_path = session.surfaces.shm_path
        if not shm_path.exists() or shm_path.stat().st_size == 0:
            return None
        built = self._build_provider(session.elf_path, shm_path)
        if session.run_id != current:
            built.close()
            return None
        self.provider = built
        self.poller = snapshot.SnapshotPoller(built)
        self.capture = regimes.Capture(built, built.regimes)
        self.provider_run = current
        return self.poller

    async def loop(self) -> None:
        """Publish S-layer snapshots while a run is live."""
        try:
            while True:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                try:
                    poller = await self.ensure_poller()
                    if poller is None:
                        continue
                    self.refresh_memory_map()
                    self.ensure_writer()
                    if self.session.paused:
                        continue
                    for obs, value in poller.tick():
                        payload = {"values": value}
                        stamp = poller.stamp(obs.topic)
                        if stamp is not None:
                            payload["ts"] = stamp
                        self.store.publish(obs.topic, Kind.SNAPSHOT, payload, src=Src.SNAP)
                        if obs.topic == observations.GUEST_TABLE:
                            self.session.adopt_guest_table(value)
                except (FileNotFoundError, snapshot.NotPublishedYet):
                    continue
                except Exception as error:
                    self.store.publish(
                        Topic.LIFE,
                        Kind.EVENT,
                        {"phase": "snapshot-unavailable", "error": str(error)},
                    )
                    self.provider_failed = self.session.run_id
                    self.drop_provider()
        finally:
            self.drop_provider()
