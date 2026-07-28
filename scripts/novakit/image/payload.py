#!/usr/bin/env python3
"""Create a checksum-pinned payload manifest for a platform build."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def integer(value: str) -> int:
    return int(value, 0)


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--guest", type=int, default=0)
    parser.add_argument("--name", default="guest")
    parser.add_argument("--load-pa", type=integer, required=True)
    parser.add_argument("--entry", type=integer, required=True)
    parser.add_argument("--memory-size", type=integer, required=True)
    args = parser.parse_args()

    try:
        record = make_record(
            args.binary,
            guest=args.guest,
            name=args.name,
            load_pa=args.load_pa,
            entry=args.entry,
            memory_size=args.memory_size,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))

    content = f"{json.dumps({'payloads': [record]}, indent=2)}\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.output.exists() or args.output.read_text() != content:
        args.output.write_text(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
