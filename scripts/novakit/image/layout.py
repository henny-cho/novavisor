#!/usr/bin/env python3
"""Validate that a linked image and its payload fit the selected board RAM."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

from .abi import read_define


class Segment(NamedTuple):
    physical_address: int
    memory_size: int


def parse_elf_header(text: str) -> int:
    match = re.search(r"Entry point address:\s*(0[xX][0-9A-Fa-f]+)", text)
    if match is None:
        raise ValueError("ELF entry point is missing")
    return int(match.group(1), 0)


def parse_program_headers(text: str) -> list[Segment]:
    segments = []
    pattern = re.compile(
        r"^\s*LOAD\s+\S+\s+\S+\s+(0[xX][0-9A-Fa-f]+)"
        r"\s+\S+\s+(0[xX][0-9A-Fa-f]+)",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        segments.append(
            Segment(
                physical_address=int(match.group(1), 0),
                memory_size=int(match.group(2), 0),
            )
        )
    if not segments:
        raise ValueError("ELF has no loadable segment")
    return segments


def validate(
    *,
    entry: int,
    segments: list[Segment],
    ram_base: int,
    ram_size: int,
    symbols: str,
    require_payload: bool,
) -> list[str]:
    errors = []
    ram_end = ram_base + ram_size
    if entry != ram_base:
        errors.append(f"entry {entry:#x} does not match RAM base {ram_base:#x}")
    for segment in segments:
        end = segment.physical_address + segment.memory_size
        if (
            segment.memory_size <= 0
            or segment.physical_address < ram_base
            or end > ram_end
        ):
            errors.append(
                f"LOAD {segment.physical_address:#x}..{end:#x} lies outside "
                f"{ram_base:#x}..{ram_end:#x}"
            )
    if require_payload and re.search(r"\bguest_image_\d+_start$", symbols, re.MULTILINE) is None:
        errors.append("embedded guest payload symbol is missing")
    return errors


def command_output(command: list[str]) -> str:
    return subprocess.run(
        command, check=True, capture_output=True, text=True
    ).stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--board-layout", type=Path, required=True)
    parser.add_argument("--readelf", required=True)
    parser.add_argument("--nm", required=True)
    parser.add_argument("--require-payload", action="store_true")
    args = parser.parse_args()

    try:
        entry = parse_elf_header(command_output([args.readelf, "-hW", args.elf]))
        segments = parse_program_headers(
            command_output([args.readelf, "-lW", args.elf])
        )
        symbols = command_output([args.nm, "-a", args.elf])
        ram_base = read_define(args.board_layout, "NOVA_BOARD_RAM_BASE")
        ram_size = read_define(args.board_layout, "NOVA_BOARD_RAM_SIZE")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))

    errors = validate(
        entry=entry,
        segments=segments,
        ram_base=ram_base,
        ram_size=ram_size,
        symbols=symbols,
        require_payload=args.require_payload,
    )
    for error in errors:
        print(f"image layout: {error}")
    if errors:
        return 1
    print(
        f"image layout passed: entry={entry:#x}, "
        f"LOAD segments={len(segments)}, payload={args.require_payload}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
