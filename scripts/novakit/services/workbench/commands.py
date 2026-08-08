"""What the host may ask a running machine for, and what it is told.

Opcodes and refusal reasons are one numbering, shared by the producer on
this side and the consumer in EL2. Both are read from the ABI header the
firmware compiles against, so an opcode added there arrives here as a
name rather than as a number nothing can spell.

The names themselves are the header's, lowercased: `NOVA_CMD_OP_SPI` is
`spi` to a reader and to the wire. Writing a second table of pretty
names would be a second place for an opcode to be renamed in.
"""

from __future__ import annotations

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
