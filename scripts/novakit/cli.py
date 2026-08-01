"""Argument parsing and command dispatch for the public automation CLI."""

from __future__ import annotations

import argparse
import io
import sys
from collections.abc import Sequence

from .commands import ci, demo, firmware, quality, workspace

# Registration order is the order `nova --help` lists the commands.
REGISTRARS = (workspace, quality, demo, firmware, ci)

# Public migrations stay out of parser registration so the canonical help tree
# has one name per operation. Remove an entry only after its callers migrate.
LEGACY_PREFIXES = (
    (("demo", "verify-all"), ("demo", "verify", "--all")),
    (("firmware", "profile"), ("firmware", "build")),
    (("objdump",), ("inspect", "disassemble")),
    (("size",), ("inspect", "size")),
    (("fmt",), ("format",)),
)


def canonical_argv(argv: Sequence[str]) -> list[str]:
    """Translate legacy command prefixes without advertising a second tree."""
    arguments = list(argv)
    for legacy, canonical in LEGACY_PREFIXES:
        if tuple(arguments[: len(legacy)]) == legacy:
            return [*canonical, *arguments[len(legacy) :]]
    return arguments


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="nova", description="NovaVisor automation")
    subcommands = root.add_subparsers(
        dest="command",
        required=True,
        title="commands",
    )
    for registrar in REGISTRARS:
        registrar.register(subcommands)
    return root


def main(argv: list[str] | None = None) -> int:
    # Progress output is evidence. Redirected stdout is block-buffered, so a
    # run killed by a job timeout loses every line it printed, and the lines
    # that do survive arrive after the command trace stderr already streamed.
    # stderr is line-buffered; match it so both read in one causal order.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    arguments = sys.argv[1:] if argv is None else argv
    args = parser().parse_args(canonical_argv(arguments))
    return args.handler(args)
