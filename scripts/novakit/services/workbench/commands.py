"""What the host may ask a running machine for, and how it asks.

Opcodes and refusal reasons are one numbering, shared by the producer
here and the consumer in EL2. Both are read from the ABI header the
firmware compiles against, so an opcode added there arrives here as a
name rather than as a number nothing can spell.

The names themselves are the header's, lowercased: `NOVA_CMD_OP_SPI` is
`spi` to a reader and to the wire. Writing a second table of pretty
names would be a second place for an opcode to be renamed in.

`Writer` is the whole of the host's reach into a running machine. QEMU
backs the guest's RAM with a shared file, so this process could write
any of it; what stops it is not discipline but the length of the
mapping, which is the ring's one page and nothing else.
"""

from __future__ import annotations

import mmap
import struct
import threading
from dataclasses import dataclass
from pathlib import Path

from ...image import abi

_OP_PREFIX = "NOVA_CMD_OP_"
_RESULT_PREFIX = "NOVA_CMD_RESULT_"

OPS: dict[str, int] = {
    name[len(_OP_PREFIX) :].lower(): code
    for name, code in abi.read_define_family(abi.COMMAND_RING, _OP_PREFIX).items()
}
RESULTS: dict[str, int] = {
    name[len(_RESULT_PREFIX) :].lower(): code
    for name, code in abi.read_define_family(abi.COMMAND_RING, _RESULT_PREFIX).items()
}

_OP_BY_CODE = {code: name for name, code in OPS.items()}
_RESULT_BY_CODE = {code: name for name, code in RESULTS.items()}


def op_name(code: int) -> str:
    """The opcode's name, or the number when this build has no such op.

    An unnamed code is not an error here. EL2 refuses what it does not
    implement and says so in the same record, and a reader looking at
    that refusal is better served by the number that was refused than by
    a reader that stopped.
    """
    return _OP_BY_CODE.get(code, str(code))


def result_name(code: int) -> str:
    return _RESULT_BY_CODE.get(code, str(code))


_LAYOUT = abi.read_defines(
    abi.COMMAND_RING,
    [
        "NOVA_CMD_MAGIC",
        "NOVA_CMD_VERSION",
        "NOVA_CMD_PAGE",
        "NOVA_CMD_SLOTS",
        "NOVA_CMD_REC_SIZE",
        "NOVA_CMD_WIDX_OFF",
        "NOVA_CMD_RIDX_OFF",
        "NOVA_CMD_RECORDS_OFF",
    ],
)
MAGIC = _LAYOUT["NOVA_CMD_MAGIC"]
VERSION = _LAYOUT["NOVA_CMD_VERSION"]
# Public: the write window is exactly this, and a test says so.
PAGE = _LAYOUT["NOVA_CMD_PAGE"]
SLOTS = _LAYOUT["NOVA_CMD_SLOTS"]
REC_SIZE = _LAYOUT["NOVA_CMD_REC_SIZE"]
_WIDX_OFF = _LAYOUT["NOVA_CMD_WIDX_OFF"]
_RIDX_OFF = _LAYOUT["NOVA_CMD_RIDX_OFF"]
_RECORDS_OFF = _LAYOUT["NOVA_CMD_RECORDS_OFF"]

# Page header, then one command. Both fixed by the ABI header the
# firmware compiles against; these strings only spell its fields.
_HEADER = struct.Struct("<QIIII")
_RECORD = struct.Struct("<QQQ")
_INDEX = struct.Struct("<Q")


class NotFormatted(RuntimeError):
    """The page carries no ring this writer understands.

    Raised rather than papered over: a wrong version or geometry means
    the firmware and this file disagree about the layout, and writing
    anyway would put plausible-looking rubbish where commands go.
    """


class NotYetFormatted(NotFormatted):
    """Nothing has been placed here yet.

    EL2 formats the page in its last init action, so an empty one right
    after launch is a moment in a boot. A build without the command
    component reads the same and never changes, which is the honest
    answer to "can this run be driven" either way.
    """


class Full(RuntimeError):
    """The ring had no slot. The command was not delivered.

    An exception rather than a return code because it is the one thing
    this direction must never do quietly: a command that vanished is a
    control that did nothing.
    """


@dataclass(frozen=True)
class Geometry:
    slots: int
    # How long a command may wait, declared by the side that drains.
    # Read rather than assumed: EL2 owns the period, and a copy here
    # would survive a change to it.
    period_us: int


_ORDER = threading.Lock()


def _release_fence() -> None:
    """Make the record visible before the index that publishes it.

    The firmware's side of this edge is a `memory_order_release` store;
    this side has no fence of its own, so it borrows one — taking and
    dropping a lock is a synchronisation operation in the C library
    underneath, and its release is the barrier. Not decoration: the
    host running the bridge is as often ARM as x86, and there two plain
    stores really can be seen in the other order by the core running the
    machine.
    """
    with _ORDER:
        pass


class Writer:
    """One run's command ring, opened for writing.

    `ram_base` is where the machine's RAM aperture starts, so the page
    at `page_pa` is mapped at `page_pa - ram_base` — the same constant
    the S layer's provider and the T layer's reader use.

    The mapping is `PAGE` bytes long. That is the security boundary and
    the whole of it: this process holds no other writable view of the
    machine, so there is no reach to be disciplined about.
    """

    def __init__(self, ram_path: Path, ram_base: int, page_pa: int, page_bytes: int):
        offset = page_pa - ram_base
        if page_bytes != PAGE:
            # `page_bytes` is what the image says the object really is.
            # The mapping's length is the boundary on what this process
            # can write, so it has to be the whole of that object and no
            # more: a global that outgrew its page would put whatever
            # follows it inside the window.
            raise NotFormatted(f"command page is {page_bytes} bytes, expected {PAGE}")
        if offset < 0 or offset % mmap.ALLOCATIONGRANULARITY:
            raise NotFormatted(f"command page at {page_pa:#x} is not on a mapping boundary")
        with Path(ram_path).open("r+b") as backing:
            self._window = mmap.mmap(
                backing.fileno(), PAGE, offset=offset, access=mmap.ACCESS_WRITE
            )
        try:
            self.geometry = self._read_geometry()
        except BaseException:
            self._window.close()
            raise

    def _read_geometry(self) -> Geometry:
        magic, version, record_size, slots, period_us = _HEADER.unpack_from(self._window, 0)
        if magic != MAGIC:
            # The magic is written last, so its absence says only that
            # nobody has finished placing a ring here.
            raise NotYetFormatted(f"no command ring on this page (magic {magic:#x})")
        if version != VERSION:
            raise NotFormatted(f"command ring version {version}, expected {VERSION}")
        if record_size != REC_SIZE:
            raise NotFormatted(f"command record is {record_size} bytes, expected {REC_SIZE}")
        if slots != SLOTS:
            raise NotFormatted(f"command ring has {slots} slots, expected {SLOTS}")
        return Geometry(slots, period_us)

    def _index(self, offset: int) -> int:
        return _INDEX.unpack_from(self._window, offset)[0]

    def pending(self) -> int:
        """Commands written and not yet taken."""
        return self._index(_WIDX_OFF) - self._index(_RIDX_OFF)

    def issue(self, op: int, a: int = 0, b: int = 0) -> int:
        """Put one command in the ring and return where it landed.

        Raises `Full` when the ring is at depth. The refusal is the
        point of this direction: EL2 drains on a declared period, so a
        full ring means the host has outrun that period, and overwriting
        would lose a command that was accepted.
        """
        write = self._index(_WIDX_OFF)
        if write - self._index(_RIDX_OFF) >= SLOTS:
            raise Full(f"command ring is full ({SLOTS} waiting)")
        _RECORD.pack_into(self._window, _RECORDS_OFF + (write % SLOTS) * REC_SIZE, op, a, b)
        _release_fence()
        _INDEX.pack_into(self._window, _WIDX_OFF, write + 1)
        return write

    def as_dict(self) -> dict:
        """What a reader needs to offer this run's controls.

        The opcodes are a build constant; the depth and the wait come
        from the page this run placed. Together because a control panel
        needs both, and apart from each other nowhere — the machine is
        the authority on what it will accept and how long it will take.
        """
        return {
            "ops": sorted(OPS),
            "slots": self.geometry.slots,
            "period_us": self.geometry.period_us,
        }

    def close(self) -> None:
        self._window.close()
