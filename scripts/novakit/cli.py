"""Argument parsing and command dispatch for the public automation CLI."""

from __future__ import annotations

import argparse

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
    args = parser().parse_args(argv)
    return args.handler(args)
