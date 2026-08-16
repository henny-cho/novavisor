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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ...image import abi

_OP_PREFIX = "NOVA_CMD_OP_"
_RESULT_PREFIX = "NOVA_CMD_RESULT_"
_ARG_PREFIX = "NOVA_CMD_ARG_"


def _family(prefix: str) -> dict[str, int]:
    return {
        name[len(prefix) :].lower(): code
        for name, code in abi.read_define_family(abi.COMMAND_RING, prefix).items()
    }


# Three vocabularies, all read the same way. A name added to the header
# arrives here as a name rather than a number nothing can spell — which
# is also why nothing else in the header may carry these prefixes.
OPS: dict[str, int] = _family(_OP_PREFIX)
RESULTS: dict[str, int] = _family(_RESULT_PREFIX)
ARGS: dict[str, int] = _family(_ARG_PREFIX)

_OP_BY_CODE = {code: name for name, code in OPS.items()}
_RESULT_BY_CODE = {code: name for name, code in RESULTS.items()}
_ARG_BY_CODE = {code: name for name, code in ARGS.items()}

COMMAND_META: dict[str, dict[str, str]] = {
    "mark": {"label": "표식", "action": "남기기", "desc": "타임라인 마크 기록"},
    "spi": {"label": "SPI", "action": "주입", "desc": "가상 SPI 인터럽트 주입"},
    "slice": {"label": "슬라이스", "action": "적용", "desc": "스케줄러 타임 슬라이스 변경"},
    "stop": {"label": "정지", "action": "정지", "desc": "대상 VM 실행 정지"},
    "reset": {"label": "리셋", "action": "리셋", "desc": "대상 VM 시스템 리셋"},
    "start": {"label": "기동", "action": "기동", "desc": "대상 VM 시스템 기동"},
}



def op_name(code: int) -> str:
    """The opcode's name, or the number when this build has no such op.

    An unnamed code is not an error: EL2 refuses what it cannot carry
    out and says so in the same record, and stopping here would drop the
    very record explaining the refusal.
    """
    return _OP_BY_CODE.get(code, str(code))


def result_name(code: int) -> str:
    return _RESULT_BY_CODE.get(code, str(code))


def arg_name(code: int) -> str:
    """What an argument means, or the number when this build has no name.

    An unnamed kind renders as a plain number rather than stopping the
    session: a reader that cannot dress an argument can still send it.
    """
    return _ARG_BY_CODE.get(code, str(code))


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
        "NOVA_CMD_NROWS_OFF",
        "NOVA_CMD_ROWSZ_OFF",
        "NOVA_CMD_OPS_OFF",
        "NOVA_CMD_OPS_CAP",
        "NOVA_CMD_OPS_ROW",
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
_OPS_OFF = _LAYOUT["NOVA_CMD_OPS_OFF"]
OPS_CAP = _LAYOUT["NOVA_CMD_OPS_CAP"]
OPS_ROW = _LAYOUT["NOVA_CMD_OPS_ROW"]

# Page header, one op row, one command. All fixed by the ABI header the
# firmware compiles against; these strings only spell its fields.
_WORD = "Q"  # one command word, at the width the record declares
_HEADER = struct.Struct("<QIIIIII")
_ROW = struct.Struct("<HBBB3x6I")
_RECORD = struct.Struct("<" + _WORD * 3)
_INDEX = struct.Struct("<" + _WORD)
# The spellings above and the layout the ABI declares are two statements
# of one thing. Tied here, because untied they part silently: a record
# grown to four words would still be packed as three and leave the rest
# of each slot holding the last command that used it, and a header field
# inserted upstream would shift every field this reader takes after it.
if _RECORD.size != REC_SIZE:
    raise SystemExit(f"command record is {REC_SIZE} bytes; this packs {_RECORD.size}")
if _ROW.size != OPS_ROW:
    raise SystemExit(f"command op row is {OPS_ROW} bytes; this packs {_ROW.size}")
if _OPS_OFF + OPS_CAP * OPS_ROW != _RECORDS_OFF:
    raise SystemExit("command page: the op rows do not end where the records begin")
_HEADER_FIELDS = (
    "NOVA_CMD_MAGIC_OFF",
    "NOVA_CMD_VERSION_OFF",
    "NOVA_CMD_RECSIZE_OFF",
    "NOVA_CMD_SLOTS_OFF",
    "NOVA_CMD_PERIOD_OFF",
    "NOVA_CMD_NROWS_OFF",
    "NOVA_CMD_ROWSZ_OFF",
)
_packed = 0
for _name, _size in zip(_HEADER_FIELDS, (8, *(4,) * 6), strict=True):
    if _LAYOUT[_name] != _packed:
        raise SystemExit(
            f"command header: {_name} is {_LAYOUT[_name]:#x}; this reader has it at {_packed:#x}"
        )
    _packed += _size
if _packed != _HEADER.size:
    raise SystemExit(f"command header packs {_HEADER.size} bytes over {_packed} of fields")
_WORD_MAX = (1 << (8 * struct.calcsize(_WORD))) - 1


@dataclass(frozen=True)
class Band:
    """What one argument word means and what it accepts.

    `lo > hi` is a free argument: any value the op takes. The defaults
    say exactly that, so a row that declares nothing about a word is
    not claiming the word is bounded to nothing.
    """

    kind: int = 0
    lo: int = 1
    hi: int = 0
    default: int = 0

    @property
    def free(self) -> bool:
        return self.lo > self.hi

    @property
    def kind_name(self) -> str:
        return arg_name(self.kind)


@dataclass(frozen=True)
class Op:
    """One opcode this build carries out, as the page advertises it.

    Named by the header's vocabulary, bounded by the machine's own
    answer. A build that does not implement an opcode publishes no row
    for it, which is the whole reason the rows exist.
    """

    code: int
    words: int = 0
    a: Band = Band()
    b: Band = Band()

    @property
    def name(self) -> str:
        return op_name(self.code)

    @property
    def args(self) -> tuple[Band, ...]:
        return (self.a, self.b)[: self.words]


def format_page(
    buffer,
    offset: int = 0,
    *,
    period_us: int,
    ops: Sequence[Op] = (),
    magic: int = MAGIC,
    version: int = VERSION,
    record_size: int = REC_SIZE,
    slots: int = SLOTS,
    row_size: int = OPS_ROW,
) -> None:
    """Lay out a command page the way EL2 lays one out.

    The reader below is the only other place this layout is spelled, so
    anything standing in for a machine builds its page here rather than
    packing the fields a second time. The overridable arguments are what
    a page can be wrong about, which is what a reader has to refuse.
    """
    _HEADER.pack_into(
        buffer, offset, magic, version, record_size, slots, period_us, len(ops), row_size
    )
    for index, op in enumerate(ops):
        _ROW.pack_into(
            buffer,
            offset + _OPS_OFF + index * OPS_ROW,
            op.code,
            op.words,
            op.a.kind,
            op.b.kind,
            op.a.lo,
            op.a.hi,
            op.a.default,
            op.b.lo,
            op.b.hi,
            op.b.default,
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
    # What this build carries out, and what each op accepts. Asked of
    # the page rather than taken from the header's vocabulary: the
    # names are what an opcode could be called, the rows are what this
    # firmware answers to.
    ops: tuple[Op, ...]


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
        magic, version, record_size, slots, period_us, rows, row_size = _HEADER.unpack_from(
            self._window, 0
        )
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
        if row_size != OPS_ROW:
            raise NotFormatted(f"command op row is {row_size} bytes, expected {OPS_ROW}")
        if rows > OPS_CAP:
            raise NotFormatted(f"command page claims {rows} op rows over {OPS_CAP}")
        return Geometry(slots=slots, period_us=period_us, ops=self._read_ops(rows))

    def _read_ops(self, rows: int) -> tuple[Op, ...]:
        out = []
        for index in range(rows):
            code, words, a_kind, b_kind, a_lo, a_hi, a_def, b_lo, b_hi, b_def = _ROW.unpack_from(
                self._window, _OPS_OFF + index * OPS_ROW
            )
            out.append(
                Op(
                    code=code,
                    # Clamped rather than trusted: the page says how many
                    # words it described, and a row is two.
                    words=min(words, 2),
                    a=Band(kind=a_kind, lo=a_lo, hi=a_hi, default=a_def),
                    b=Band(kind=b_kind, lo=b_lo, hi=b_hi, default=b_def),
                )
            )
        return tuple(out)

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

        One op per row the machine published, each carrying the number
        of arguments it reads and what each one means and accepts — so
        a panel builds a control from the answer rather than from a
        list of opcodes it was written against.

        The names come from the ABI header both sides compile against;
        everything else comes from the page this run placed.
        """
        return {
            "ops": [
                {
                    "name": op.name,
                    "code": op.code,
                    "args": [
                        {
                            "kind": arg.kind_name,
                            "lo": arg.lo,
                            "hi": arg.hi,
                            "default": arg.default,
                            "free": arg.free,
                        }
                        for arg in op.args
                    ],
                }
                for op in self.geometry.ops
            ],
            "slots": self.geometry.slots,
            "period_us": self.geometry.period_us,
        }

    def close(self) -> None:
        self._window.close()
