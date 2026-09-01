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

from ...image import elfsym, observe
from .. import expect
from . import commands, hardware, regimes, snapshot, trace
from .observations import COMMAND_PAGE, OBSERVATIONS
from .snapshot import image_symbols, open_provider

# A terminal waits at the same cadence a scenario does.
_POLL_SECONDS = expect.POLL_SECONDS


class Machine:
    """One run's readable surfaces, opened on first use.

    The readers stay open across the steps of a scenario: an `event`
    step must see records that arrived while an earlier step waited, and
    a reader reopened per step would restart its cursor and re-report
    the whole ring.
    """

    def __init__(self, elf: Path, shm: Path, *, on_reading=None):
        self._elf = Path(elf)
        self._shm = Path(shm)
        # What a predicate read, offered to whoever else is watching this
        # run. One read, two audiences: a screen showing a different
        # value from the one that was judged would be a second reader.
        self._on_reading = on_reading
        self._view: observe.View | None = None
        self._provider = None
        self._capture: regimes.Capture | None = None
        self._tracer: trace.TraceReader | None = None
        self._writer: commands.Writer | None = None
        self._records: list[trace.Record] = []
        self._anchor = 0

    @property
    def board(self) -> dict[str, int]:
        return hardware.platform()

    def view(self) -> observe.View:
        if self._view is None:
            self._view = observe.view_of(self._elf)
        return self._view

    def provider(self):
        if self._provider is None:
            self._provider = open_provider(
                self._elf, self._shm, self.board["NOVA_BOARD_PHYS_RAM_BASE"], self.view()
            )
        return self._provider

    def reading(self, topic: str) -> object:
        """This topic's value now.

        No cursor is passed, so the publisher answers with the value
        every time rather than with news about someone else's copy: a
        scenario asks what a topic reads, and two steps of one run may
        be asking about the same one.
        """
        if topic in self.view().absent:
            raise KeyError(f"this composition does not publish {topic}")
        for obs in OBSERVATIONS:
            if obs.topic == topic:
                value = self.provider().read(obs).value
                if self._on_reading is not None:
                    self._on_reading(topic, value)
                return value
        raise KeyError(f"this build publishes no observation named {topic}")

    def walk(self, regime: str, address: str) -> dict | None:
        """Where one address in one regime lands, and what it lands on.

        None while EL2 has not published its tables, which is ordinary
        during a boot.

        Assembled where every caller assembles it: the capture holds the
        topology at the two rates its halves move at, and the provider
        is the one reader both the tables and the bank rooting them come
        from. Refreshed on every look because a guest's own regimes
        appear when it turns its MMU on, which is usually after the step
        before this one carried.
        """
        if self._capture is None:
            self._capture = regimes.Capture(self.provider(), self.provider().regimes)
        self._capture.refresh()
        if self._capture.topology is None:
            return None
        return regimes.answer(
            self._capture.topology,
            {"regime": regime, "address": address},
            live=self.provider(),
        )

    def records(self) -> list[trace.Record]:
        """Everything the rings have produced so far in this run.

        Drained cumulatively rather than per look: a step may be
        satisfied by a record that arrived while an earlier step was
        still waiting, and a reader that only returned the newest batch
        would drop it between two polls.
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

    def after_anchor(self):
        """Decoded records the scenario has not yet moved past, with
        their index.

        A step list is a narrative, and the ring is where its order can
        actually be settled: console output and trace records reach this
        process by different routes, so only records can be placed
        against records.
        """
        for index in range(self._anchor, len(self.records())):
            yield index, trace.decode(self._records[index])

    def anchor_at(self, index: int) -> None:
        """Move the line between what is past and what this run is still
        waiting for.

        An observation moves it to what was seen; an action moves it to
        the moment it was issued, because EL2 emits a command's verdict
        *after* carrying it out — so the effects of a command are older
        records than its answer, and anchoring on the answer would put
        them out of reach.
        """
        self._anchor = max(self._anchor, index)

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


# Not ready yet, as against wrong: these mean "look again", and one tuple
# because two handlers spelling it differently ended a run at 0.1s the
# other waited out. observe.Stale is wrong, not early — all of them say
# "rebuild" — so polling it only delayed the same answer by the wait.
NOT_YET = (FileNotFoundError, snapshot.NotPublishedYet, elfsym.TornRead)


def _at(fields: dict, name: str) -> object:
    """One field, named by path: `el1.tcr` descends where the value does.

    A reading is shaped like the firmware struct it came from, so some of
    what a step wants to name is a member of a member. A flat name would
    stringify the whole inner record and compare that, which passes and
    fails for reasons nobody wrote down.
    """
    held: object = fields
    for part in name.split("."):
        if not isinstance(held, dict):
            return ""
        held = held.get(part, "")
    return held


def _matches(fields: dict, wanted: dict) -> bool:
    return all(str(_at(fields, name)) == str(value) for name, value in wanted.items())


def _select(value: object, where: dict) -> dict | None:
    """The entry a step is talking about, or None while there is none.

    A topic that reads as a list needs a `where` to say which entry; one
    that reads as a record is itself. Equality is over named fields, so
    a topic that reads as neither cannot be the subject of one.
    """
    if isinstance(value, dict):
        return value if _matches(value, where) else None
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
                value = machine.reading(topic)
            except KeyError as error:
                # A topic this build does not publish is a manifest that
                # cannot be satisfied by any run, not a slow boot.
                return expect.step_failed(str(error))
            except NOT_YET:
                return expect.PENDING
            entry = _select(value, where)
            if entry is None:
                return expect.step_pending(
                    f"nothing in {topic} matches {where}")
            if _matches(entry, wanted):
                return expect.CARRIED
            seen = {name: _at(entry, name) for name in wanted}
            return expect.step_pending(f"{topic} reads {seen}, wanted {wanted}")

        return poll

    return handler


def _walked(answer: dict) -> dict:
    """A walk's answer as a step names it: one line, VA to PA.

    Four names, and they are the ones a person reads off the screen —
    where this regime's walk ended, where the translation beneath took
    that output, whether it faulted, and whether the chain moved under
    the walk. The answer nests them, because that is the shape a map is
    drawn from; a step is a sentence about whether the chain closed.
    """
    probe = answer.get("probe", {})
    beneath = answer.get("through", {}).get("probe", {})
    return {
        "output": probe.get("output"),
        "through": beneath.get("output"),
        "fault": probe.get("fault"),
        "moving": answer.get("moving"),
    }


def walk_handler(machine: Machine) -> expect.StepHandler:
    """One address, walked in one regime, closing to a physical address.

    A question this process asks and answers, where a `command` is one
    the machine carries out — so the two are separate words rather than
    one that would leave a demo unable to say which happened.

    Everything a run is not ready for yet waits: a guest that has not
    turned its MMU on has no regime to name, and a bank caught mid-copy
    is a moment. Only an address that is not an address fails outright,
    since no run can satisfy it.
    """

    def handler(step: dict):
        regime = str(step["walk"])
        wanted = step.get("equals", {})
        address = str(step.get("address", ""))
        try:
            regimes.address_of(address)
        except ValueError as error:
            refusal = str(error)
            return lambda: expect.step_failed(refusal)

        def poll() -> expect.StepOutcome:
            try:
                answer = machine.walk(regime, address)
            except NOT_YET:
                return expect.PENDING
            except (KeyError, ValueError) as error:
                # No such regime yet, or a half the guest has not enabled.
                return expect.step_pending(str(error))
            if answer is None:
                return expect.step_pending("this run has published no page tables")
            walked = _walked(answer)
            if _matches(walked, wanted):
                return expect.CARRIED
            seen = {name: walked.get(name) for name in wanted}
            return expect.step_pending(f"{regime} walks {address} to {seen}, wanted {wanted}")

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
            lost = 0
            try:
                for index, fields in machine.after_anchor():
                    if fields["event"] == wanted_event and _matches(fields, where):
                        machine.anchor_at(index + 1)
                        return expect.CARRIED
                    if fields["event"] == "trace.gap":
                        lost += int(fields.get("count", 0))
            except (FileNotFoundError, trace.NotFormatted):
                return expect.PENDING
            if lost:
                return expect.step_pending(
                    f"{lost} records were lost to a full ring while this step waited, "
                    f"so {wanted_event} may have happened unseen"
                )
            return expect.PENDING

        return poll

    return handler


def issue(machine: Machine, text: str) -> str:
    """Put one command in the ring. Empty when it went, else why not.

    Anchors before writing: EL2 emits a verdict after carrying a command
    out, so everything the command causes is an older record than its
    answer, and a reader that anchored on the answer could not reach
    them.
    """
    words = text.split()
    writer = machine.writer()
    offered = {op["name"]: op for op in writer.as_dict()["ops"]}
    if words[0] not in offered:
        return (f"this run offers no {words[0]} command "
                f"(it offers {', '.join(sorted(offered)) or 'none'})")
    machine.anchor_at(len(machine.records()))
    writer.issue(commands.OPS[words[0]], *(int(word, 0) for word in words[1:]))
    return ""


def verdict(machine: Machine, op_name: str) -> str:
    """What the machine answered this command, or empty while it has not."""
    for _index, fields in machine.after_anchor():
        if fields["event"] == "command" and fields["op"] == op_name:
            return fields["result"]
    return ""


def command_handler(machine: Machine) -> expect.StepHandler:
    """Issue one command, then wait for the verdict it caused.

    The verdict is a trace record rather than a return value, so the ring
    answers this step the same way it answers an `event` one.
    """

    def handler(step: dict):
        text = str(step["command"])
        op_name = text.split()[0]
        wanted = step.get("expect_result", "ok")
        state = {"issued": False}

        def poll() -> expect.StepOutcome:
            if not state["issued"]:
                try:
                    refusal = issue(machine, text)
                except (FileNotFoundError, commands.NotYetFormatted):
                    return expect.PENDING  # EL2 has not published it yet
                except commands.NotFormatted as error:
                    return expect.step_failed(str(error))
                if refusal:
                    return expect.step_failed(refusal)
                state["issued"] = True
                return expect.PENDING
            answer = verdict(machine, op_name)
            if not answer:
                return expect.PENDING
            if answer == wanted:
                return expect.CARRIED
            return expect.step_failed(f"{op_name} answered {answer}, wanted {wanted}")

        return poll

    return handler


def handlers_for(machine: Machine) -> dict[str, expect.StepHandler]:
    return {
        "observe": observe_handler(machine),
        "event": event_handler(machine),
        "command": command_handler(machine),
        "walk": walk_handler(machine),
    }


def carry_out(machine: Machine, text: str, seconds: float, sleep) -> tuple[str, str]:
    """Issue one command from a terminal and wait for its verdict.

    Returns the machine's own result name and a line to print. Shares
    `issue` and `verdict` with the scenario step, so the wait for the
    page to be advertised and the reading of the answer have one
    implementation and two callers.
    """
    op_name = text.split()[0]
    waited = 0.0

    def out_of_time(what: str) -> tuple[str, str]:
        return "", f"{text}: {what} within {seconds:g}s"

    while True:
        try:
            refusal = issue(machine, text)
        except commands.NotFormatted as error:
            # NotYetFormatted is the ordinary case during a boot: EL2
            # publishes the page in its last init action.
            if not isinstance(error, commands.NotYetFormatted):
                return "", str(error)
        except FileNotFoundError:
            pass  # the backing file is not there yet either
        else:
            if refusal:
                return "", refusal
            break
        if waited >= seconds:
            return out_of_time("no command ring was advertised")
        sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS

    while True:
        answer = verdict(machine, op_name)
        if answer:
            return answer, f"{text} -> {answer}"
        if waited >= seconds:
            return out_of_time("no verdict")
        sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS
