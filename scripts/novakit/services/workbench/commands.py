"""What the host may ask a running machine for, and how it asks.

Opcodes and refusal reasons are one numbering shared with EL2, read from
the ABI header the firmware compiles against — so an opcode added there
arrives here as a name rather than a number nothing can spell. The names
are the header's, lowercased: `NOVA_CMD_OP_SPI` is `spi` to a reader and
to the wire.

`Writer` is the whole of the host's reach into a running machine. QEMU
backs the guest's RAM with a shared file, so this process could write
any of it; what stops it is not discipline but the mapping's length,
which is the ring's one page.
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

    An unnamed code is not an error: EL2 refuses what it cannot carry
    out and says so in the same record, and stopping here would drop the
    very record explaining the refusal.
    """
    return _OP_BY_CODE.get(code, str(code))


def result_name(code: int) -> str:
    return _RESULT_BY_CODE.get(code, str(code))


_LAYOUT = abi.read_defines(
    abi.COMMAND_RING,
    [
        "NOVA_CMD_MAGIC",
        "NOVA_CMD_VERSION",
        "NOVA_CMD_ANSWER_SHIFT",
        "NOVA_CMD_ANSWER_MASK",
        "NOVA_CMD_PAGE",
        "NOVA_CMD_SLOTS",
        "NOVA_CMD_REC_SIZE",
        "NOVA_CMD_WIDX_OFF",
        "NOVA_CMD_RIDX_OFF",
        "NOVA_CMD_RECORDS_OFF",
        "NOVA_CMD_MAGIC_OFF",
        "NOVA_CMD_VERSION_OFF",
        "NOVA_CMD_RECSIZE_OFF",
        "NOVA_CMD_SLOTS_OFF",
        "NOVA_CMD_PERIOD_OFF",
        "NOVA_CMD_SLICE_MIN_OFF",
        "NOVA_CMD_SLICE_DEF_OFF",
        "NOVA_CMD_SLICE_MAX_OFF",
        "NOVA_CMD_SPI_LO_OFF",
        "NOVA_CMD_SPI_HI_OFF",
    ],
)
MAGIC = _LAYOUT["NOVA_CMD_MAGIC"]
VERSION = _LAYOUT["NOVA_CMD_VERSION"]
# How the answering record packs the opcode and the verdict into one
# word. Read rather than restated: a shift spelled on both sides is a
# reader that keeps decoding after the writer moved the halves.
ANSWER_SHIFT = _LAYOUT["NOVA_CMD_ANSWER_SHIFT"]
ANSWER_MASK = _LAYOUT["NOVA_CMD_ANSWER_MASK"]
# Public: the write window is exactly this, and a test says so.
PAGE = _LAYOUT["NOVA_CMD_PAGE"]
SLOTS = _LAYOUT["NOVA_CMD_SLOTS"]
REC_SIZE = _LAYOUT["NOVA_CMD_REC_SIZE"]
_WIDX_OFF = _LAYOUT["NOVA_CMD_WIDX_OFF"]
_RIDX_OFF = _LAYOUT["NOVA_CMD_RIDX_OFF"]
_RECORDS_OFF = _LAYOUT["NOVA_CMD_RECORDS_OFF"]

# Page header, then one command. Both fixed by the ABI header the
# firmware compiles against; these strings only spell its fields.
_WORD = "Q"  # one command word, at the width the record declares
_HEADER = struct.Struct("<QIIIIIIIII")
_RECORD = struct.Struct("<" + _WORD * 3)
_INDEX = struct.Struct("<" + _WORD)
# The spellings above and the layout the ABI declares are two statements
# of one thing. Tied here, because untied they part silently: a record
# grown to four words would still be packed as three and leave the rest
# of each slot holding the last command that used it, and a header field
# inserted upstream would shift every field this reader takes after it.
if _RECORD.size != REC_SIZE:
    raise SystemExit(f"command record is {REC_SIZE} bytes; this packs {_RECORD.size}")
_HEADER_FIELDS = (
    "NOVA_CMD_MAGIC_OFF",
    "NOVA_CMD_VERSION_OFF",
    "NOVA_CMD_RECSIZE_OFF",
    "NOVA_CMD_SLOTS_OFF",
    "NOVA_CMD_PERIOD_OFF",
    "NOVA_CMD_SLICE_MIN_OFF",
    "NOVA_CMD_SLICE_DEF_OFF",
    "NOVA_CMD_SLICE_MAX_OFF",
    "NOVA_CMD_SPI_LO_OFF",
    "NOVA_CMD_SPI_HI_OFF",
)
_packed = 0
for _name, _size in zip(_HEADER_FIELDS, (8, *(4,) * 9), strict=True):
    if _LAYOUT[_name] != _packed:
        raise SystemExit(
            f"command header: {_name} is {_LAYOUT[_name]:#x}; this reader has it at {_packed:#x}"
        )
    _packed += _size
if _packed != _HEADER.size:
    raise SystemExit(f"command header packs {_HEADER.size} bytes over {_packed} of fields")
_WORD_MAX = (1 << (8 * struct.calcsize(_WORD))) - 1


def format_page(
    buffer,
    offset: int = 0,
    *,
    period_us: int,
    slice_us: tuple[int, int, int],
    spi_intids: tuple[int, int],
    magic: int = MAGIC,
    version: int = VERSION,
    record_size: int = REC_SIZE,
    slots: int = SLOTS,
) -> None:
    """Lay out a command page the way EL2 lays one out.

    The reader above is the only other place this header is spelled, so
    anything standing in for a machine builds its page here rather than
    packing the fields a second time. The overridable arguments are what
    a page can be wrong about, which is what a reader has to refuse.
    """
    _HEADER.pack_into(
        buffer, offset, magic, version, record_size, slots, period_us, *slice_us, *spi_intids
    )

class NotFormatted(RuntimeError):
    """The page carries no ring this writer understands.

    A wrong version or geometry means the firmware and this file
    disagree about the layout, and writing anyway would put
    plausible-looking rubbish where commands go.
    """


class NotYetFormatted(NotFormatted):
    """Nothing has been placed here yet.

    EL2 formats the page in its last init action, so an empty one right
    after launch is a moment in a boot. A build without the component
    reads the same and never changes — the honest answer to "can this
    run be driven" either way.
    """


class Full(RuntimeError):
    """The ring had no slot; the command was not delivered.

    An exception rather than a return code: this is the one thing the
    direction must never do quietly.
    """


# The producer's own refusal, named from the vocabulary EL2 shares —
# every other reason is a code in the answering record, and this one has
# no record, so the word is all a reader gets. A reason renamed in the
# header fails here rather than leaving the old word behind.
_FULL = result_name(RESULTS["full"])


@dataclass(frozen=True)
class Geometry:
    slots: int
    # How long a command may wait, declared by the side that drains.
    # Read rather than assumed: a copy here would survive a change to it.
    period_us: int
    # The bands EL2 checks an argument against, in the page it placed.
    # A panel offering anything else would be offering what this machine
    # refuses; asking it is the only way to be sure of the firmware in
    # front of us rather than the sources it might have been built from.
    slice_us: tuple[int, int, int]
    spi_intids: tuple[int, int]


_ORDER = threading.Lock()


def _release_fence() -> None:
    """Make the record visible before the index that publishes it.

    The firmware's side of this edge is a `memory_order_release` store.
    Python has no fence, so this borrows one: taking and dropping a lock
    is a synchronisation operation underneath, and its release is the
    barrier. Not decoration — the host running the bridge is as often
    ARM as x86, and there two plain stores really can be observed in the
    other order by the core running the machine.
    """
    with _ORDER:
        pass


class Writer:
    """One run's command ring, opened for writing.

    `ram_base` is where the machine's RAM aperture starts, so the page
    at `page_pa` is mapped at `page_pa - ram_base` — the same constant
    the S layer's provider and the T layer's reader use.

    The mapping is `PAGE` bytes long, and that is the whole boundary:
    this process holds no other writable view of the machine.
    """

    def __init__(self, ram_path: Path, ram_base: int, page_pa: int, page_bytes: int):
        offset = page_pa - ram_base
        if page_bytes != PAGE:
            # What the image says the object really is. The mapping has
            # to be the whole of it and no more: a global that outgrew
            # its page would put whatever follows it inside the window.
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
        (magic, version, record_size, slots, period_us, slice_min, slice_def, slice_max, spi_lo,
         spi_hi) = _HEADER.unpack_from(self._window, 0)
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
        return Geometry(
            slots=slots,
            period_us=period_us,
            slice_us=(slice_min, slice_def, slice_max),
            spi_intids=(spi_lo, spi_hi),
        )

    def _index(self, offset: int) -> int:
        return _INDEX.unpack_from(self._window, offset)[0]

    def issue(self, op: int, a: int = 0, b: int = 0) -> int:
        """Put one command in the ring and return where it landed.

        Raises `Full` when the ring is at depth. The refusal is the
        point of this direction: EL2 drains on a declared period, so a
        full ring means the host has outrun that period, and overwriting
        would lose a command that was accepted.

        A word too wide for the record is a `ValueError` here rather
        than a packing failure escaping into the socket loop. EL2 checks
        every argument it acts on; this checks the one thing EL2 never
        sees, which is a number that could not be written down.
        """
        for word in (op, a, b):
            if not 0 <= word <= _WORD_MAX:
                raise ValueError(f"a command word must be 0..{_WORD_MAX:#x}, not {word}")
        write = self._index(_WIDX_OFF)
        if write - self._index(_RIDX_OFF) >= SLOTS:
            raise Full(f"{_FULL} — {SLOTS} commands already waiting")
        _RECORD.pack_into(self._window, _RECORDS_OFF + (write % SLOTS) * REC_SIZE, op, a, b)
        _release_fence()
        _INDEX.pack_into(self._window, _WIDX_OFF, write + 1)
        return write

    def as_dict(self) -> dict:
        """What a reader needs to offer this run's controls.

        The opcodes are named by the ABI header both sides compile
        against; everything else comes from the page this run placed.
        Copied nowhere: the machine is the authority on what it accepts
        and how long it takes to answer.
        """
        return {
            "ops": sorted(OPS),
            "slots": self.geometry.slots,
            "period_us": self.geometry.period_us,
            "slice_us": list(self.geometry.slice_us),
            "spi_intids": list(self.geometry.spi_intids),
        }

    def close(self) -> None:
        self._window.close()
