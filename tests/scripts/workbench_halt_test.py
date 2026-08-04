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


def serve_gdb(listener: socket.socket) -> None:
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
            reply = handle_gdb(payload, documents)
            checksum = sum(reply.encode()) % 256
            connection.sendall(f"+${reply}#{checksum:02x}".encode())
    connection.close()


def handle_gdb(payload: str, documents: dict[str, str]) -> str:
    if payload.startswith("qSupported"):
        return "PacketSize=1000;qXfer:features:read+"
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
                connection.sendall(b'{"return": {"running": true}}\n')
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
                client.stop()
                self.assertTrue(client.running())
                client.cont()
            finally:
                client.close()
                listener.close()
            self.assertEqual(log, ["qmp_capabilities", "stop", "query-status", "cont"])


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


class PauseRollbackTest(unittest.TestCase):
    def test_failed_sweep_resumes_the_machine(self):
        """stop lands, the gdb surface is gone: pause() must roll the
        stop back (cont) before re-raising, or the machine stays frozen
        with the UI still reading "running"."""
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            qmp_path = Path(directory) / "qmp.sock"
            listener = unix_listener(qmp_path)
            log: list[str] = []
            thread = threading.Thread(
                target=serve_qmp, args=(listener, log, 2), daemon=True
            )
            thread.start()

            inspector = halt.HaltInspector(qmp_path, Path(directory) / "absent.sock")
            try:
                with self.assertRaises(OSError):
                    inspector.pause()
            finally:
                listener.close()
            self.assertEqual(log, ["qmp_capabilities", "stop", "qmp_capabilities", "cont"])


if __name__ == "__main__":
    unittest.main()
