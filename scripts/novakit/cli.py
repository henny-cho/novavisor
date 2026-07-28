"""Argument parsing and command dispatch for the public automation CLI."""

from __future__ import annotations

import argparse
import io
import sys

from .commands import build, check, ci, demo, firmware

# Registration order is the order `nova --help` lists the commands.
COMMANDS = (build, check, ci, demo, firmware)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="nova", description="NovaVisor automation")
    subcommands = root.add_subparsers(dest="command", required=True)
    for command in COMMANDS:
        command.register(subcommands)
    return root


def main(argv: list[str] | None = None) -> int:
    # Progress output is evidence. Redirected stdout is block-buffered, so a
    # run killed by a job timeout loses every line it printed, and the lines
    # that do survive arrive after the command trace stderr already streamed.
    # stderr is line-buffered; match it so both read in one causal order.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    args = parser().parse_args(argv)
    return args.handler(args)
