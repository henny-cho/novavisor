"""Carrying out the steps a console cannot: read state, wait for an
event, drive the machine.

The readers and the writer are the bridge's own — the provider the
poller reads, the ring reader the timeline drains, the page writer the
drive panel uses. What this module adds is a predicate over each, and
the seam that hands them to a scenario without the scenario learning
what a shared memory region is.

Everything opens lazily. A run's surfaces exist from the launch, but
the regions inside them do not: EL2 formats its rings and publishes its
command page during init, so a step reaching for one before then waits
rather than fails. That is the whole of the "wait until it is
advertised" rule, and it lives here so no caller repeats it.
"""

from __future__ import annotations

from pathlib import Path

from ...image import observe
from .. import expect
from . import commands, hardware, trace
from .observations import COMMAND_PAGE, OBSERVATIONS
from .snapshot import image_symbols, open_provider


class Machine:
    """One run's readable surfaces, opened on first use.

    The readers stay open across the steps of a scenario: an `event`
    step must see records that arrived while an earlier step waited, and
    a reader reopened per step would restart its cursor and re-report
    the whole ring.
    """

    def __init__(self, elf: Path, shm: Path):
        self._elf = Path(elf)
        self._shm = Path(shm)
        self._provider = None
        self._tracer: trace.TraceReader | None = None
        self._writer: commands.Writer | None = None
        self._records: list[trace.Record] = []

    @property
    def board(self) -> dict[str, int]:
        return hardware.platform()

    def provider(self):
        if self._provider is None:
            self._provider = open_provider(
                self._elf,
                self._shm,
                self.board["NOVA_BOARD_PHYS_RAM_BASE"],
                observe.view_of(self._elf),
            )
        return self._provider

    def reading(self, topic: str) -> object:
        for obs in OBSERVATIONS:
            if obs.topic == topic:
                return self.provider().read(obs)
        raise KeyError(f"this build publishes no observation named {topic}")

    def records(self) -> list[trace.Record]:
        """Everything the rings have produced so far in this run.

        Drained cumulatively: a step asks whether something happened,
        not whether it happened since the last look.
        """
        if self._tracer is None:
            self._tracer = trace.TraceReader(
                self._shm,
                self.board["NOVA_BOARD_PHYS_RAM_BASE"],
                self.board["NOVA_BOARD_TRACE_PA"],
                self.board["NOVA_BOARD_TRACE_SIZE"],
            )
        self._records += self._tracer.drain()
        return self._records

    def writer(self) -> commands.Writer:
        if self._writer is None:
            symbols = image_symbols(self.provider())
            if symbols is None:
                raise commands.NotFormatted("this image publishes no command page")
            page, size = symbols.extent_of(COMMAND_PAGE)
            self._writer = commands.Writer(
                self._shm, self.board["NOVA_BOARD_PHYS_RAM_BASE"], page, size
            )
        return self._writer

    def close(self) -> None:
        for holder in (self._writer, self._tracer, self._provider):
            close = getattr(holder, "close", None)
            if close is not None:
                close()


def _matches(fields: dict, wanted: dict) -> bool:
    return all(str(fields.get(name, "")) == str(value) for name, value in wanted.items())


def _select(value: object, where: dict) -> dict | None:
    """The one entry a `where` names, out of a topic that reads as a list."""
    if not where:
        return value if isinstance(value, dict) else {"value": value}
    if not isinstance(value, list):
        return None
    for entry in value:
        if isinstance(entry, dict) and _matches(entry, where):
            return entry
    return None


def observe_handler(machine: Machine) -> expect.StepHandler:
    """A reading equals what the step says it must.

    Only equality, and only on a value that stays: a sampler asked about
    a moment answers whichever side of it the look landed on.
    """

    def handler(step: dict):
        topic = step["observe"]
        where = step.get("where", {})
        wanted = step.get("equals", {})

        def poll() -> expect.StepOutcome:
            try:
                entry = _select(machine.reading(topic), where)
            except (FileNotFoundError, observe.Stale, KeyError) as error:
                # A region that is not there yet is ordinary during boot;
                # a manifest naming a topic this build lacks is not, and
                # the deadline turns it into a failure that says so.
                return expect.step_failed(str(error)) if isinstance(error, KeyError) \
                    else expect.PENDING
            if entry is None:
                return expect.PENDING
            if _matches(entry, wanted):
                return expect.CARRIED
            return expect.PENDING

        return poll

    return handler


def event_handler(machine: Machine) -> expect.StepHandler:
    """A record this run produced, matched by its named fields.

    A hole in the ring is reported as itself. "It did not happen" and
    "the reader was too far behind to see it" are different findings,
    and only one of them is a defect in the machine.
    """

    def handler(step: dict):
        wanted_event = step["event"]
        where = step.get("where", {})

        def poll() -> expect.StepOutcome:
            try:
                records = machine.records()
            except (FileNotFoundError, trace.NotFormatted):
                return expect.PENDING
            lost = 0
            for record in records:
                fields = trace.decode(record)
                if fields["event"] == wanted_event and _matches(fields, where):
                    return expect.CARRIED
                if fields["event"] == "trace.gap":
                    lost += int(fields.get("count", 0))
            if lost:
                return expect.step_pending(
                    f"{lost} records were lost to a full ring during this run, "
                    f"so {wanted_event} may have happened unseen"
                )
            return expect.PENDING

        return poll

    return handler


def command_handler(machine: Machine) -> expect.StepHandler:
    """Issue one command, then wait for the verdict it caused.

    The verdict is a trace record rather than a return value, so the
    ring is read for it the same way an `event` step reads for anything
    else — and the wait for the page to be advertised is the same wait.
    """

    def handler(step: dict):
        words = str(step["command"]).split()
        wanted = step.get("expect_result", "ok")
        state = {"issued": False}

        def poll() -> expect.StepOutcome:
            if not state["issued"]:
                try:
                    writer = machine.writer()
                except (FileNotFoundError, commands.NotYetFormatted):
                    return expect.PENDING  # EL2 has not published it yet
                except commands.NotFormatted as error:
                    return expect.step_failed(str(error))
                offered = {op["name"]: op for op in writer.as_dict()["ops"]}
                name, args = words[0], [int(word, 0) for word in words[1:]]
                if name not in offered:
                    return expect.step_failed(
                        f"this run offers no {name} command "
                        f"(it offers {', '.join(sorted(offered)) or 'none'})"
                    )
                writer.issue(commands.OPS[name], *args)
                state["issued"] = True
                return expect.PENDING
            for record in machine.records():
                fields = trace.decode(record)
                if fields["event"] != "command" or fields["op"] != words[0]:
                    continue
                if fields["result"] == wanted:
                    return expect.CARRIED
                return expect.step_failed(
                    f"{words[0]} answered {fields['result']}, wanted {wanted}"
                )
            return expect.PENDING

        return poll

    return handler


def handlers_for(machine: Machine) -> dict[str, expect.StepHandler]:
    return {
        "observe": observe_handler(machine),
        "event": event_handler(machine),
        "command": command_handler(machine),
    }
