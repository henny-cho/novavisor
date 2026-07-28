"""One payload record: the binary, its digest, and where it is placed.

A library, not a program: the build graph never runs this, it is the shape
that services/artifacts.py and services/tfa.py write manifests from.
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
    data = binary.read_bytes()
    if not data:
        raise ValueError("payload binary must not be empty")
    if guest < 0 or load_pa < 0 or entry < 0 or memory_size <= 0:
        raise ValueError("payload placement must be non-negative and non-empty")
    return {
        "guest": guest,
        "name": name,
        "binary": str(binary),
        "sha256": hashlib.sha256(data).hexdigest(),
        "load_pa": load_pa,
        "entry": entry,
        "memory_size": memory_size,
    }
