"""One payload record: the binary, its digest, and where it is placed.

A library, not a program: the build graph never runs this, it is the shape
that services/artifacts.py and services/tfa.py write manifests from.

A formatter and nothing more. Every record written here is read back by
the DTB generator, which checks the same fields against the guest table
it is generating for — an empty binary, a placement that is not the
guest's, an entry outside guest memory. Repeating a weaker form of those
checks here would only decide which of the two rejects a bad record
first.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def make_record(
    binary: Path,
    *,
    guest: int,
    name: str,
    load_pa: int,
    entry: int,
    memory_size: int,
) -> dict:
    binary = binary.resolve()
    return {
        "guest": guest,
        "name": name,
        "binary": str(binary),
        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "load_pa": load_pa,
        "entry": entry,
        "memory_size": memory_size,
    }
