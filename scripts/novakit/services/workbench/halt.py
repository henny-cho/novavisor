"""The H layer: halt-and-inspect over QMP and the gdb remote protocol.

Everything here is synchronous socket work the bridge runs in an
executor. QMP owns stop/cont — the virtual clock freezes with the
machine, so a paused inspection never breaks watchdogs — and the
gdbstub supplies per-CPU register truth while stopped.

Measured limit (QEMU 9/10, aarch64): the sysreg XML carries the EL2
control set (HCR/VTTBR/VTCR/CNTVOFF/SCTLR/ELR/SPSR...) but no ICH_*/
ICC_* — the GIC CPU interface is not exported to gdb. Resident LR
inspection therefore stays with the S layer's shadow.
"""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path

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
    """Minimal QMP: one line-delimited JSON command at a time."""

    def __init__(self, path: Path, timeout: float = 5.0):
        self._sock = socket.socket(socket.AF_UNIX)
        self._sock.settimeout(timeout)
        self._sock.connect(str(path))
        self._reader = self._sock.makefile("r")
        banner = json.loads(self._reader.readline())
        if "QMP" not in banner:
            raise ConnectionError(f"not a QMP socket: {banner}")
        self.execute("qmp_capabilities")

    def execute(self, command: str) -> dict:
        self._sock.sendall(json.dumps({"execute": command}).encode() + b"\n")
        while True:
            reply = json.loads(self._reader.readline())
            if "event" in reply:
                continue  # asynchronous events interleave freely
            if "error" in reply:
                raise RuntimeError(f"QMP {command}: {reply['error']}")
            return reply["return"]

    def stop(self) -> None:
        self.execute("stop")

    def cont(self) -> None:
        self.execute("cont")

    def running(self) -> bool:
        return bool(self.execute("query-status").get("running"))

    def close(self) -> None:
        self._reader.close()
        self._sock.close()


class GdbClient:
    """Just enough of the remote protocol to read registers by name.

    Register numbers are positional: the stub's target.xml (with its
    includes, in order) assigns them sequentially unless a reg carries
    an explicit regnum.
    """

    def __init__(self, path: Path, timeout: float = 5.0):
        self._sock = socket.socket(socket.AF_UNIX)
        self._sock.settimeout(timeout)
        self._sock.connect(str(path))
        self._buffer = b""
        self._exchange("qSupported:xmlRegisters=aarch64")
        self.registers = self._read_layout()
        self.threads = self._read_threads()

    # -------- packet framing (ack mode) --------

    def _exchange(self, payload: str) -> str:
        checksum = sum(payload.encode()) % 256
        self._sock.sendall(f"${payload}#{checksum:02x}".encode())
        while True:
            start = self._buffer.find(b"$")
            end = self._buffer.find(b"#", start)
            if start != -1 and end != -1 and len(self._buffer) >= end + 3:
                packet = self._buffer[start + 1 : end].decode()
                self._buffer = self._buffer[end + 3 :]
                self._sock.sendall(b"+")
                return packet
            self._buffer += self._sock.recv(65536)

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

    def close(self) -> None:
        self._sock.close()


class HaltInspector:
    """One pause: stop the machine, read every core, stay stopped."""

    def __init__(self, qmp_path: Path, gdb_path: Path):
        self._qmp_path = qmp_path
        self._gdb_path = gdb_path

    def pause(self) -> dict:
        qmp = QmpClient(self._qmp_path)
        try:
            qmp.stop()
        finally:
            qmp.close()
        gdb = GdbClient(self._gdb_path)
        try:
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
        finally:
            gdb.close()

    def resume(self) -> None:
        qmp = QmpClient(self._qmp_path)
        try:
            qmp.cont()
        finally:
            qmp.close()
