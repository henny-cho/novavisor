"""The H layer's protocol clients against scripted fake servers."""

from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from novakit.services.workbench import halt

TARGET_XML = (
    '<?xml version="1.0"?><target>'
    '<xi:include href="core.xml"/>'
    "</target>"
)
CORE_XML = (
    "<feature>"
    '<reg name="x0" bitsize="64" regnum="0"/>'
    '<reg name="pc" bitsize="64" regnum="32"/>'
    '<reg name="HCR_EL2" bitsize="64"/>'  # sequential: 33
    '<reg name="VTTBR_EL2" bitsize="64"/>'  # sequential: 34
    "</feature>"
)


def serve_gdb(listener: socket.socket, log: list[str] | None = None) -> None:
    connection, _ = listener.accept()
    connection.settimeout(5)
    documents = {"target.xml": TARGET_XML, "core.xml": CORE_XML}
    buffer = b""
    while True:
        try:
            data = connection.recv(65536)
        except OSError:
            break
        if not data:
            break
        buffer += data
        while True:
            # A bare 0x03 is the interrupt request; it carries no frame.
            if buffer.startswith(b"\x03"):
                buffer = buffer[1:]
                if log is not None:
                    log.append("\x03")
                connection.sendall(b"+$T02thread:01;#00")
                continue
            start = buffer.find(b"$")
            end = buffer.find(b"#", start)
            if start == -1 or end == -1 or len(buffer) < end + 3:
                break
            payload = buffer[start + 1 : end].decode()
            buffer = buffer[end + 3 :]
            if log is not None:
                log.append(payload)
            reply = handle_gdb(payload, documents)
            if reply is None:
                continue  # a silent stub: the machine is still running
            checksum = sum(reply.encode()) % 256
            connection.sendall(f"+${reply}#{checksum:02x}".encode())
    connection.close()


def handle_gdb(payload: str, documents: dict[str, str]) -> str | None:
    if payload.startswith("qSupported"):
        return "PacketSize=1000;qXfer:features:read+;vContSupported+"
    if payload == "vCont?":
        return "vCont;c;C;s;S"
    if payload.startswith("qXfer:features:read:"):
        _, _, _, annex, span = payload.split(":")
        offset, length = (int(part, 16) for part in span.split(","))
        text = documents[annex]
        chunk = text[offset : offset + length]
        return ("l" if offset + length >= len(text) else "m") + chunk
    if payload == "qfThreadInfo":
        return "m01,02"
    if payload == "qsThreadInfo":
        return "l"
    if payload.startswith("Hg"):
        return "OK"
    if payload.startswith(("Z", "z")):
        return "OK"
    if payload.startswith("vCont;s"):
        return "T05thread:01;"
    if payload == "vCont;c":
        return None  # runs on: a continue answers only when it stops
    if payload == "D":
        return "OK"
    if payload.startswith("p"):
        number = int(payload[1:], 16)
        # pc=32 -> 0x1111...; HCR=33 -> 0x80000039 LE; unknown -> error
        if number == 32:
            return "1122334455667788"
        if number == 33:
            return (0x8000_0039).to_bytes(8, "little").hex()
        return "E01"
    return ""


def serve_qmp(listener: socket.socket, log: list[str], sessions: int = 1) -> None:
    for _ in range(sessions):
        connection, _ = listener.accept()
        connection.settimeout(5)
        connection.sendall(b'{"QMP": {"version": {}}}\n')
        reader = connection.makefile("r")
        for line in reader:
            request = json.loads(line)
            log.append(request["execute"])
            if request["execute"] == "query-status":
                connection.sendall(
                    b'{"return": {"running": true, "status": "running"}}\n'
                )
            else:
                connection.sendall(b'{"event": "SOMETHING"}\n{"return": {}}\n')
        connection.close()


def serve_eof(listener: socket.socket) -> None:
    """Accept one client and hang up without sending a byte."""
    connection, _ = listener.accept()
    connection.close()


def unix_listener(path: Path) -> socket.socket:
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    listener.listen(1)
    return listener


class GdbClientTest(unittest.TestCase):
    def test_layout_threads_and_reads(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            path = Path(directory) / "gdb.sock"
            listener = unix_listener(path)
            thread = threading.Thread(target=serve_gdb, args=(listener,), daemon=True)
            thread.start()

            client = halt.GdbClient(path)
            try:
                self.assertEqual(client.threads, ["01", "02"])
                self.assertEqual(client.registers["pc"], (32, 64))
                self.assertEqual(client.registers["HCR_EL2"], (33, 64))
                self.assertEqual(client.registers["VTTBR_EL2"], (34, 64))
                self.assertTrue(client.select_thread("01"))
                self.assertEqual(client.read_register("pc"), 0x8877665544332211)
                self.assertEqual(client.read_register("HCR_EL2"), 0x8000_0039)
                self.assertIsNone(client.read_register("VTTBR_EL2"))  # E01
                self.assertIsNone(client.read_register("ICH_LR0_EL2"))  # absent
            finally:
                client.close()
                listener.close()


class QmpClientTest(unittest.TestCase):
    def test_handshake_commands_and_event_skipping(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            path = Path(directory) / "qmp.sock"
            listener = unix_listener(path)
            log: list[str] = []
            thread = threading.Thread(target=serve_qmp, args=(listener, log), daemon=True)
            thread.start()

            client = halt.QmpClient(path)
            try:
                self.assertTrue(client.running())
                self.assertEqual(client.status(), "running")
            finally:
                client.close()
                listener.close()
            self.assertEqual(
                log, ["qmp_capabilities", "query-status", "query-status"]
            )


class DeadPeerTest(unittest.TestCase):
    """EOF from a dying QEMU must surface as ConnectionError, never as a
    JSON decode error or an infinite recv loop."""

    def test_qmp_eof_raises_connection_error(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            path = Path(directory) / "qmp.sock"
            listener = unix_listener(path)
            thread = threading.Thread(target=serve_eof, args=(listener,), daemon=True)
            thread.start()
            try:
                with self.assertRaises(ConnectionError):
                    halt.QmpClient(path)
            finally:
                listener.close()

    def test_gdb_eof_raises_connection_error(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            path = Path(directory) / "gdb.sock"
            listener = unix_listener(path)
            thread = threading.Thread(target=serve_eof, args=(listener,), daemon=True)
            thread.start()
            try:
                with self.assertRaises(ConnectionError):
                    halt.GdbClient(path)
            finally:
                listener.close()


def serve_gdb_then_hang_up(listener: socket.socket) -> None:
    """Complete the handshake, then die once the sweep starts.

    This is the failure that matters now: attaching already stopped the
    machine, so a sweep that dies afterwards must not leave the stop
    held.
    """
    connection, _ = listener.accept()
    connection.settimeout(5)
    documents = {"target.xml": TARGET_XML, "core.xml": CORE_XML}
    buffer = b""
    while True:
        try:
            data = connection.recv(65536)
        except OSError:
            break
        if not data:
            break
        buffer += data
        while True:
            start = buffer.find(b"$")
            end = buffer.find(b"#", start)
            if start == -1 or end == -1 or len(buffer) < end + 3:
                break
            payload = buffer[start + 1 : end].decode()
            buffer = buffer[end + 3 :]
            if payload.startswith("Hg"):
                connection.close()
                return
            reply = handle_gdb(payload, documents)
            checksum = sum((reply or "").encode()) % 256
            connection.sendall(f"+${reply or ''}#{checksum:02x}".encode())


class AdvanceTest(unittest.TestCase):
    """Stepping, continuing, interrupting — the packets that turn a
    frozen machine into one that can be walked forward."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir="/dev/shm")
        path = Path(self.directory.name) / "gdb.sock"
        self.listener = unix_listener(path)
        self.log: list[str] = []
        threading.Thread(
            target=serve_gdb, args=(self.listener, self.log), daemon=True
        ).start()
        self.client = halt.GdbClient(path)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.listener.close)
        self.addCleanup(self.client.close)

    def test_vcont_is_queried_so_the_actions_are_known(self):
        self.assertEqual(self.client.actions, "vCont;c;C;s;S")
        self.assertIn("vCont?", self.log)

    def test_step_reports_the_stop(self):
        self.assertEqual(self.client.step("01"), "T05thread:01;")
        self.assertIn("vCont;s:01", self.log)

    def test_continue_returns_none_while_the_machine_runs(self):
        """A continue is answered only when something stops the machine.
        Silence is the normal case, not a fault — treating it as one
        would turn every healthy run into an error."""
        self.assertIsNone(self.client.cont(timeout=0.2))

    def test_interrupt_stops_a_running_machine(self):
        self.assertIsNone(self.client.cont(timeout=0.2))
        self.assertEqual(self.client.interrupt(), "T02thread:01;")
        self.assertIn("\x03", self.log)

    def test_breakpoints_use_framed_packets(self):
        self.assertEqual(self.client._exchange("Z0,4000bb24,4"), "OK")
        self.assertEqual(self.client._exchange("z0,4000bb24,4"), "OK")


class StopOwnershipTest(unittest.TestCase):
    def test_pause_holds_the_connection_and_resume_releases_it(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            gdb_path = Path(directory) / "gdb.sock"
            listener = unix_listener(gdb_path)
            log: list[str] = []
            threading.Thread(
                target=serve_gdb, args=(listener, log), daemon=True
            ).start()

            inspector = halt.HaltInspector(Path(directory) / "qmp.sock", gdb_path)
            try:
                self.assertFalse(inspector.paused)
                data = inspector.pause()
                self.assertTrue(inspector.paused)
                self.assertEqual(len(data["cpus"]), 2)
                # Held, not reconnected: a second pause reuses the stop.
                inspector.pause()
                self.assertEqual(log.count("qSupported:xmlRegisters=aarch64"), 1)
                inspector.resume()
                self.assertFalse(inspector.paused)
                self.assertIn("D", log)
            finally:
                listener.close()

    def test_failed_sweep_releases_the_machine(self):
        """Attaching *is* the stop, so a sweep that dies afterwards must
        hand the machine back — otherwise it stays frozen with the UI
        still showing a live pause button."""
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            gdb_path = Path(directory) / "gdb.sock"
            listener = unix_listener(gdb_path)
            threading.Thread(
                target=serve_gdb_then_hang_up, args=(listener,), daemon=True
            ).start()

            inspector = halt.HaltInspector(Path(directory) / "qmp.sock", gdb_path)
            try:
                with self.assertRaises(ConnectionError):
                    inspector.pause()
                self.assertFalse(inspector.paused)
            finally:
                listener.close()

    def test_advancing_an_unpaused_machine_is_refused(self):
        """Attaching is the stop, so every advance needs the held
        connection. Asked on a running machine each must say so rather
        than reach for a socket that was never opened."""
        inspector = halt.HaltInspector(Path("/nonexistent"), Path("/nonexistent"))
        self.assertFalse(inspector.paused)
        advances = {
            "step": lambda: inspector.step(1),
            "begin": lambda: inspector.begin(["trap"]),
            "wait": lambda: inspector.wait(0.01),
            "where": inspector.where,
            "interrupt": inspector.interrupt,
        }
        for name, advance in advances.items():
            with self.subTest(advance=name), self.assertRaises(RuntimeError):
                advance()


class ArmTest(unittest.TestCase):
    """Arming is declarative: the caller states the set it wants and the
    difference is applied. A UI that forgets to clear one cannot leave
    the machine stopping somewhere nobody asked about."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir="/dev/shm")
        gdb_path = Path(self.directory.name) / "gdb.sock"
        self.listener = unix_listener(gdb_path)
        self.log: list[str] = []
        threading.Thread(
            target=serve_gdb, args=(self.listener, self.log), daemon=True
        ).start()
        self.inspector = halt.HaltInspector(
            Path(self.directory.name) / "qmp.sock", gdb_path
        )
        # Stand in for the per-run symbol resolution; the addresses
        # themselves are covered against the real image elsewhere.
        self.inspector._addresses = {"vgic.bind": 0x4000BB24, "trap": 0x400044D8}
        self.inspector.pause()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.listener.close)
        self.addCleanup(self.inspector.resume)

    def test_arming_sets_only_what_was_asked_for(self):
        self.assertEqual(self.inspector.arm(["vgic.bind"]), ["vgic.bind"])
        self.assertIn("Z0,4000bb24,4", self.log)
        self.assertNotIn("Z0,400044d8,4", self.log)

    def test_rearming_clears_what_is_no_longer_wanted(self):
        self.inspector.arm(["vgic.bind", "trap"])
        self.assertEqual(self.inspector.armed, ["trap", "vgic.bind"])
        self.inspector.arm(["trap"])
        self.assertEqual(self.inspector.armed, ["trap"])
        self.assertIn("z0,4000bb24,4", self.log)

    def test_rearming_the_same_set_does_not_resend(self):
        self.inspector.arm(["trap"])
        self.inspector.arm(["trap"])
        self.assertEqual(self.log.count("Z0,400044d8,4"), 1)

    def test_unknown_names_are_ignored_not_guessed(self):
        self.assertEqual(self.inspector.arm(["nope", "trap"]), ["trap"])

    def test_resume_forgets_the_breakpoints_the_stub_dropped(self):
        """Letting go of the connection drops the stub's breakpoints, so
        keeping the names would make the next pause believe they are
        still set and skip re-arming them."""
        self.inspector.arm(["trap"])
        self.inspector.resume()
        self.assertEqual(self.inspector.armed, [])


class StopReplyTest(unittest.TestCase):
    def test_fields_are_parsed(self):
        self.assertEqual(halt.parse_stop("T05thread:01;"), {"thread": "01"})
        self.assertEqual(
            halt.parse_stop("T05swbreak:;thread:p1.2;"),
            {"swbreak": "", "thread": "p1.2"},
        )

    def test_a_non_stop_reply_yields_nothing(self):
        self.assertEqual(halt.parse_stop("OK"), {})
        self.assertEqual(halt.parse_stop(""), {})

    def test_payload_hexes_every_number(self):
        """Arguments are bit patterns and counters alike; JSON numbers
        lose exactness past 2^53, so they travel as strings."""
        stop = halt.Stop(0x4000BB24, "01", "vgic.bind", "post", {"vintid": 37})
        self.assertEqual(
            stop.payload(),
            {
                "pc": "0x4000bb24",
                "thread": "01",
                "event": "vgic.bind",
                "edge": "post",
                "args": {"vintid": "0x25"},
            },
        )


def serve_gdb_stalling(listener: socket.socket, log: list[str]) -> None:
    """A stub whose steps never retire — the `wfi` case.

    The hypervisor idles in `nova::halt()`, `wfi; b .`, and a step there
    completes only when an interrupt arrives. Most of the time between
    events is spent here, so this is the ordinary case, not an edge one.
    """
    connection, _ = listener.accept()
    connection.settimeout(5)
    documents = {"target.xml": TARGET_XML, "core.xml": CORE_XML}
    buffer = b""
    while True:
        try:
            data = connection.recv(65536)
        except OSError:
            break
        if not data:
            break
        buffer += data
        while True:
            if buffer.startswith(b"\x03"):
                buffer = buffer[1:]
                log.append("\x03")
                connection.sendall(b"+$T02thread:01;#00")
                continue
            start = buffer.find(b"$")
            end = buffer.find(b"#", start)
            if start == -1 or end == -1 or len(buffer) < end + 3:
                break
            payload = buffer[start + 1 : end].decode()
            buffer = buffer[end + 3 :]
            log.append(payload)
            if payload.startswith("vCont;s"):
                continue  # the instruction never retires
            reply = handle_gdb(payload, documents)
            if reply is None:
                continue
            checksum = sum(reply.encode()) % 256
            connection.sendall(f"+${reply}#{checksum:02x}".encode())
    connection.close()


class AdvanceModeTest(unittest.TestCase):
    """Stepping, running to an event, and taking the machine back."""

    def start(self, server=serve_gdb):
        directory = tempfile.TemporaryDirectory(dir="/dev/shm")
        self.addCleanup(directory.cleanup)
        gdb_path = Path(directory.name) / "gdb.sock"
        listener = unix_listener(gdb_path)
        self.addCleanup(listener.close)
        log: list[str] = []
        threading.Thread(target=server, args=(listener, log), daemon=True).start()
        inspector = halt.HaltInspector(Path(directory.name) / "qmp.sock", gdb_path)
        # The stub answers pc as the wire-order hex 1122...88, which decodes
        # little-endian to this.
        inspector._addresses = {"vgic.bind": 0x8877665544332211, "trap": 0x400044D8}
        inspector.pause()
        self.addCleanup(inspector.resume)
        return inspector, log

    def test_step_follows_the_thread_that_reported(self):
        inspector, log = self.start()
        result = inspector.step(3)
        self.assertEqual(result["steps"], 3)
        self.assertFalse(result["stalled"])
        self.assertEqual(log.count("vCont;s:01"), 3)

    def test_a_step_that_never_retires_is_reported_not_hung(self):
        """`wfi` is where the hypervisor waits. A UI that hangs there
        looks broken; one that says "still waiting" is telling the truth
        about the machine."""
        inspector, log = self.start(serve_gdb_stalling)
        result = inspector.step(4, timeout=0.2)
        self.assertEqual(result["steps"], 0)
        self.assertTrue(result["stalled"])
        # The machine is taken back rather than left mid-instruction.
        self.assertIn("\x03", log)

    def test_where_names_the_catalogued_event_and_reads_its_arguments(self):
        """The fake stub's pc decodes to the address the map calls
        vgic.bind — so the arguments come back named."""
        inspector, _ = self.start()
        stop = inspector.where()
        self.assertEqual(stop.event, "vgic.bind")
        self.assertEqual(stop.edge, "post")
        self.assertEqual(set(stop.args), {"vm", "vintid", "pintid", "generation"})

    def test_a_stop_somewhere_uncatalogued_says_so(self):
        inspector, _ = self.start()
        inspector._addresses = {"trap": 0x400044D8}
        stop = inspector.where()
        self.assertEqual(stop.event, "")
        self.assertEqual(stop.args, {})

    def test_waiting_is_sliced_so_an_abort_can_land(self):
        """A machine that never reaches the chosen event must not pin
        the caller: each slice returns None and the continue stands."""
        inspector, log = self.start()
        inspector.begin(["trap"])
        self.assertIsNone(inspector.wait(0.15))
        self.assertIsNone(inspector.wait(0.15))
        self.assertEqual(log.count("vCont;c"), 1)

    def test_interrupt_takes_a_running_machine_back(self):
        inspector, log = self.start()
        inspector.begin(["trap"])
        self.assertIsNone(inspector.wait(0.15))
        stop = inspector.interrupt()
        self.assertEqual(stop.thread, "01")
        self.assertIn("\x03", log)
