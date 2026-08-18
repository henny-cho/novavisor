"""The H layer: halt-and-inspect over the gdb remote protocol.

Everything here is synchronous socket work the bridge runs in an
executor.

**One stop owner.** Attaching to QEMU's stub stops the machine by
itself, so the gdb connection *is* the pause: it is held for as long as
the machine is stopped and dropped to let it run again. A QMP `stop`
must not be mixed in — measured against QEMU 9/10, a QMP-stopped machine
answers no `vCont` and no `Z` packet at all, with no error, so the two
owners deadlock silently rather than failing. QMP is therefore read-only
here: it reports the run state and nothing else.

The virtual clock freezes with the machine either way, so a paused
inspection never breaks watchdogs.

Measured limit (QEMU 9/10, aarch64): the sysreg XML carries the EL2
control set (HCR/VTTBR/VTCR/CNTVOFF/SCTLR/ELR/SPSR...) but no ICH_*/
ICC_* — the GIC CPU interface is not exported to gdb. Resident LR
inspection therefore stays with the S layer's shadow.
"""

from __future__ import annotations

import json
import re
import socket
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ...image import elfsym
from . import events

_XML_REFERENCE = re.compile(r'href="([^"]+)"')
_XML_REGISTER = re.compile(r"<reg\b[^>]*/>")
_XML_ATTRIBUTE = re.compile(r'(\w+)="([^"]+)"')

# Read per CPU while stopped, in this order; names must match the
# stub's XML. `pc` comes from the core feature, the rest from sysregs.
INSPECT_REGISTERS = (
    "pc",
    "HCR_EL2",
    "VTTBR_EL2",
    "VTCR_EL2",
    "SCTLR_EL2",
    "CNTVOFF_EL2",
    "CNTV_CTL_EL0",
    "CNTV_CVAL_EL0",
    "ELR_EL2",
    "SPSR_EL2",
)


class QmpClient:
    """Minimal QMP: one line-delimited JSON command at a time.

    Read-only by construction. `stop`/`cont` belong to the gdb stub (see
    the module docstring); offering them here again would re-create the
    deadlock this layer was reorganised to remove.
    """

    def __init__(self, path: Path, timeout: float = 5.0):
        self._sock = socket.socket(socket.AF_UNIX)
        self._sock.settimeout(timeout)
        try:
            self._sock.connect(str(path))
            self._reader = self._sock.makefile("r")
            banner = self._readline()
            if "QMP" not in banner:
                raise ConnectionError(f"not a QMP socket: {banner}")
            self.execute("qmp_capabilities")
        except BaseException:
            self._sock.close()
            raise

    def _readline(self) -> dict:
        line = self._reader.readline()
        if not line:
            # readline() returns '' on EOF and after a socket timeout;
            # json.loads('') would blur both into a decode error.
            raise ConnectionError("QMP connection closed")
        return json.loads(line)

    def execute(self, command: str) -> dict:
        self._sock.sendall(json.dumps({"execute": command}).encode() + b"\n")
        while True:
            reply = self._readline()
            if "event" in reply:
                continue  # asynchronous events interleave freely
            if "error" in reply:
                raise RuntimeError(f"QMP {command}: {reply['error']}")
            return reply["return"]

    def status(self) -> str:
        return str(self.execute("query-status").get("status", "unknown"))

    def running(self) -> bool:
        return bool(self.execute("query-status").get("running"))

    def close(self) -> None:
        self._reader.close()
        self._sock.close()


class GdbClient:
    """Just enough of the remote protocol to stop, read, and advance.

    Register numbers are positional: the stub's target.xml (with its
    includes, in order) assigns them sequentially unless a reg carries
    an explicit regnum.
    """

    def __init__(self, path: Path, timeout: float = 5.0):
        self._sock = socket.socket(socket.AF_UNIX)
        self._sock.settimeout(timeout)
        try:
            self._sock.connect(str(path))
            self._buffer = b""
            self._exchange("qSupported:xmlRegisters=aarch64")
            # Asking is what enables vCont on some stubs, and the reply
            # says which actions are real rather than merely advertised.
            self.actions = self._exchange("vCont?")
            self.registers = self._read_layout()
            self.threads = self._read_threads()
        except BaseException:
            self._sock.close()
            raise

    # -------- packet framing (ack mode) --------

    def _send(self, payload: str) -> None:
        checksum = sum(payload.encode()) % 256
        self._sock.sendall(f"${payload}#{checksum:02x}".encode())

    def _receive(self, timeout: float | None) -> str | None:
        """One framed packet, or None if the peer stayed silent.

        None is not an error: `vCont;c` answers only when the machine
        stops, which may be much later or never.
        """
        self._sock.settimeout(timeout)
        while True:
            start = self._buffer.find(b"$")
            end = self._buffer.find(b"#", start)
            if start != -1 and end != -1 and len(self._buffer) >= end + 3:
                packet = self._buffer[start + 1 : end].decode()
                self._buffer = self._buffer[end + 3 :]
                self._sock.sendall(b"+")
                return packet
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                return None
            if not chunk:
                # A dying stub returns b'' forever; without this the loop
                # would spin an executor thread at 100% for the process
                # lifetime and block interpreter exit.
                raise ConnectionError("gdb stub closed the connection")
            self._buffer += chunk

    def _exchange(self, payload: str, timeout: float = 5.0) -> str:
        """A request that must be answered; a silent peer is a fault."""
        self._send(payload)
        reply = self._receive(timeout)
        if reply is None:
            raise ConnectionError(f"gdb stub did not answer {payload!r}")
        return reply

    def _read_document(self, annex: str) -> str:
        text = ""
        offset = 0
        while True:
            chunk = self._exchange(f"qXfer:features:read:{annex}:{offset:x},4000")
            if not chunk or chunk[0] not in "lm":
                raise ConnectionError(f"features read failed for {annex}: {chunk[:40]}")
            text += chunk[1:]
            offset += len(chunk) - 1
            if chunk.startswith("l"):
                return text

    def _read_layout(self) -> dict[str, tuple[int, int]]:
        document = self._read_document("target.xml")
        for reference in _XML_REFERENCE.findall(document):
            document += self._read_document(reference)
        layout: dict[str, tuple[int, int]] = {}
        number = 0
        for register in _XML_REGISTER.findall(document):
            attributes = dict(_XML_ATTRIBUTE.findall(register))
            if "regnum" in attributes:
                number = int(attributes["regnum"])
            layout[attributes["name"]] = (number, int(attributes.get("bitsize", "64")))
            number += 1
        return layout

    def _read_threads(self) -> list[str]:
        threads: list[str] = []
        reply = self._exchange("qfThreadInfo")
        while reply.startswith("m"):
            threads += reply[1:].split(",")
            reply = self._exchange("qsThreadInfo")
        return threads

    # -------- reads --------

    def select_thread(self, thread: str) -> bool:
        return self._exchange(f"Hg{thread}") == "OK"

    def read_register(self, name: str) -> int | None:
        entry = self.registers.get(name)
        if entry is None:
            return None
        number, bitsize = entry
        reply = self._exchange(f"p{number:x}")
        if not reply or reply.startswith("E"):
            return None
        return int.from_bytes(bytes.fromhex(reply)[: bitsize // 8], "little")

    # -------- advancing the machine --------

    def cont(self, timeout: float | None = None) -> str | None:
        """Run until something stops us. None means still running."""
        self._send("vCont;c")
        return self._receive(timeout)

    def step(self, thread: str, timeout: float = 5.0) -> str | None:
        """One instruction on one thread.

        None means the instruction has not retired — which is the normal
        answer for `wfi`, where the hypervisor's idle loop sits and where
        no amount of waiting helps until an interrupt arrives.
        """
        self._send(f"vCont;s:{thread}")
        return self._receive(timeout)

    def interrupt(self, timeout: float = 5.0) -> str | None:
        """Ctrl-C: ask a running machine to stop."""
        self._sock.sendall(b"\x03")
        return self._receive(timeout)

    def set_breakpoint(self, address: int) -> bool:
        """Break at an address.

        Z0 rather than Z1: QEMU keeps both kinds out of band — a read
        back from a breakpointed address returns the original
        instruction, so EL2's read-only text is no obstacle — but the
        hardware kind is limited to the debug registers the CPU models,
        which is fewer than this catalogue has entries.
        """
        return self._exchange(f"Z0,{address:x},4") == "OK"

    def clear_breakpoint(self, address: int) -> bool:
        return self._exchange(f"z0,{address:x},4") == "OK"

    def drain(self) -> int:
        """Discard stop replies already in flight.

        A step that timed out is still owed an answer; when the machine
        is taken back by force both arrive, and leaving the second one
        queued would make the *next* command read a reply to the last.
        """
        dropped = 0
        while self._receive(0.05) is not None:
            dropped += 1
        return dropped

    def detach(self) -> None:
        """Release the machine. The stub resumes it as we let go."""
        try:
            self._send("D")
            self._receive(2.0)
        except OSError:
            pass  # the machine is gone; nothing to hand back

    def close(self) -> None:
        self._sock.close()


_STOP_FIELD = re.compile(r"(\w+):([^;]*);")


@dataclass(frozen=True)
class Stop:
    """Where the machine came to rest, and what it means.

    `event` is the catalogue name when the PC matches a known stop point
    and empty otherwise — an interrupt or a step lands wherever it lands,
    and calling that "unknown" is more honest than guessing.
    """

    pc: int
    thread: str
    event: str = ""
    edge: str = ""
    args: dict[str, int] = field(default_factory=dict)

    def payload(self) -> dict:
        return {
            "pc": f"{self.pc:#x}",
            "thread": self.thread,
            "event": self.event,
            "edge": self.edge,
            # Bit patterns and counters alike travel as hex strings, the
            # same rule the register sweep follows.
            "args": {name: f"{value:#x}" for name, value in self.args.items()},
        }


def parse_stop(reply: str) -> dict[str, str]:
    """The `key:value;` pairs of a `T05thread:01;` stop reply.

    The two-digit signal follows the T with no separator, so it has to
    be cut before matching — `\\w+` would otherwise read `T05thread` as
    one key and the thread would never be found.
    """
    if not reply.startswith("T"):
        return {}
    return dict(_STOP_FIELD.findall(reply[3:]))


class HaltInspector:
    """The machine's stop, owned for as long as it is held.

    `pause()` takes ownership by attaching and returns the register
    sweep; `resume()` gives it back. Between the two the connection
    stays open, which is what lets the machine be advanced instead of
    only read.
    """

    def __init__(
        self, qmp_path: Path, gdb_path: Path, symbols: elfsym.SymbolTable | None = None
    ):
        self._qmp_path = qmp_path
        self._gdb_path = gdb_path
        self._symbols = symbols
        self._gdb: GdbClient | None = None
        self._addresses: dict[str, int] | None = None
        self._armed: dict[str, int] = {}
        self._thread = ""
        self._running = False

    @property
    def paused(self) -> bool:
        return self._gdb is not None

    @property
    def armed(self) -> list[str]:
        return sorted(self._armed)

    def addresses(self) -> dict[str, int]:
        """Where each catalogued event sits in *this* build.

        From the symbol table the build wrote down — a breakpoint needs
        an entry address, not a layout, and a table that is small and
        complete answers questions it was never aimed at.
        """
        if self._addresses is None:
            if self._symbols is None:
                raise RuntimeError("no image to resolve breakpoints against")
            self._addresses = {
                event.id: self._symbols.address_of(event.symbol) for event in events.STOPS
            }
        return self._addresses

    def arm(self, wanted: Iterable[str]) -> list[str]:
        """Break at exactly these events, and nowhere else.

        Declarative on purpose: the caller says which stops it wants and
        the difference is applied, so a UI that forgets to clear one
        cannot leave the machine stopping somewhere nobody asked about.
        """
        gdb = self._require()
        where = self.addresses()
        target = {name for name in wanted if name in where}
        for name in sorted(set(self._armed) - target):
            gdb.clear_breakpoint(self._armed.pop(name))
        for name in sorted(target - set(self._armed)):
            if gdb.set_breakpoint(where[name]):
                self._armed[name] = where[name]
        return self.armed

    def status(self) -> str:
        qmp = QmpClient(self._qmp_path)
        try:
            return qmp.status()
        finally:
            qmp.close()

    def pause(self) -> dict:
        if self._gdb is not None:
            return self._sweep()
        gdb = GdbClient(self._gdb_path)
        self._gdb = gdb
        try:
            return self._sweep()
        except BaseException:
            # Attaching already stopped the machine. Reporting a failure
            # while holding it would strand the UI on a live pause
            # button, so hand it back before re-raising.
            self.resume()
            raise

    def _sweep(self) -> dict:
        gdb = self._require()
        cpus = []
        for thread in gdb.threads:
            if not gdb.select_thread(thread):
                continue
            # u64 values travel as hex strings: JSON numbers lose
            # precision past 2^53 and these are bit patterns anyway.
            cpus.append(
                {
                    name: None if value is None else f"{value:#x}"
                    for name in INSPECT_REGISTERS
                    for value in (gdb.read_register(name),)
                }
            )
        return {"cpus": cpus, "registers": list(INSPECT_REGISTERS)}

    def _require(self) -> GdbClient:
        if self._gdb is None:
            raise RuntimeError("the machine is not paused")
        return self._gdb

    # -------- advancing --------

    def _note(self, reply: str | None) -> None:
        """Remember which thread reported, so a step follows the stop."""
        thread = parse_stop(reply or "").get("thread", "")
        if thread:
            self._thread = thread.split(".")[-1]

    def _thread_id(self) -> str:
        gdb = self._require()
        return self._thread or (gdb.threads[0] if gdb.threads else "1")

    def where(self) -> Stop:
        """Decode the current stop: which catalogued event, if any.

        Arguments are read at the entry breakpoint, where AAPCS64 still
        has them in x0.. — a few instructions later they are gone, which
        is why the catalogue breaks on entry rather than anywhere else
        in the function.
        """
        gdb = self._require()
        thread = self._thread_id()
        gdb.select_thread(thread)
        pc = gdb.read_register("pc") or 0
        by_address = {address: name for name, address in (self._addresses or {}).items()}
        name = by_address.get(pc, "")
        event = events.BY_ID.get(name)
        args = {}
        if event is not None:
            args = {
                label: gdb.read_register(f"x{index}") or 0
                for index, label in enumerate(event.args)
                if label
            }
        return Stop(pc, thread, name, event.edge if event else "", args)

    def step(self, count: int = 1, timeout: float = 3.0) -> dict:
        """Advance one thread by instructions.

        Measured at ~700 us per instruction over the RSP socket, so this
        is for looking *inside* an event, never for reaching one: a few
        hundred steps is a fraction of a second, a few million is a day.

        A step that does not retire is the normal answer at `wfi`, where
        the hypervisor idles between events — the instruction completes
        only when an interrupt arrives. Saying so beats hanging.
        """
        gdb = self._require()
        thread = self._thread_id()
        done = 0
        stalled = False
        for _ in range(max(1, count)):
            reply = gdb.step(thread, timeout)
            if reply is None:
                stalled = True
                self._note(gdb.interrupt())
                gdb.drain()
                break
            self._note(reply)
            done += 1
        return {"steps": done, "stalled": stalled, "stop": self.where().payload()}

    def begin(self, wanted: Iterable[str]) -> None:
        """Arm the wanted stops and let the machine go.

        Split from the waiting so a caller can stay cancellable: the
        continue is sent once, and the reply is collected in slices.
        """
        gdb = self._require()
        self.arm(wanted)
        self._running = True
        gdb._send("vCont;c")

    def wait(self, timeout: float) -> Stop | None:
        """A slice of waiting. None means the machine is still going."""
        gdb = self._require()
        reply = gdb._receive(timeout)
        if reply is None:
            return None
        self._running = False
        self._note(reply)
        return self.where()

    def interrupt(self) -> Stop:
        """Take a running machine back."""
        gdb = self._require()
        if self._running:
            self._note(gdb.interrupt())
            gdb.drain()
            self._running = False
        return self.where()

    def resume(self) -> None:
        gdb, self._gdb = self._gdb, None
        # Breakpoints live in the stub, so letting go drops them; keeping
        # the names would make the next pause think they were still set.
        self._armed.clear()
        self._thread = ""
        self._running = False
        if gdb is None:
            return
        try:
            gdb.detach()
        finally:
            gdb.close()
