"""The H layer's protocol clients against scripted fake servers."""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from novakit.services.workbench import halt  # noqa: E402

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

    def test_stop_and_cont_are_not_offered(self):
        """The stub owns the stop. A QMP-stopped machine answers no vCont
        at all — with no error — so a second owner deadlocks the
        inspector silently. Keeping these off QmpClient is what makes
        that unrepresentable rather than merely discouraged."""
        for name in ("stop", "cont"):
            self.assertFalse(hasattr(halt.QmpClient, name), name)


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
        inspector = halt.HaltInspector(Path("/nonexistent"), Path("/nonexistent"))
        with self.assertRaises(RuntimeError):
            inspector._require()


if __name__ == "__main__":
    unittest.main()
