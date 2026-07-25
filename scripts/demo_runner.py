#!/usr/bin/env python3
"""
NovaVisor demo harness — command-line entry point.

Reads a demo's manifest.yml, builds the hypervisor and demo guest(s),
launches QEMU with embedded or external-loader payloads, and verifies
that expected output patterns appear within their per-pattern deadlines.
The implementation lives in the `novademo` package next to this file.

Exits 0 on PASS, non-zero on any failure. CI gates on this exit code.

Demos are addressed by the numeric ID shown in `list` (the NN_ prefix of
the demo directory, e.g. `2` or `02` for 02_timer); the full directory
name is also accepted for scripts that already store it.

Usage:
    demo_runner.py list
    demo_runner.py fetch <id|name>      # populate the external image cache
    demo_runner.py run <id|name>        # launch without pattern checking
    demo_runner.py verify <id|name>     # launch and check manifest.expect
    demo_runner.py verify-repeat <id|name> --runs N
    demo_runner.py verify-all           # run all enabled demos sequentially
"""

from __future__ import annotations

import argparse
import sys

from novademo import commands, manifest


def main() -> int:
    p = argparse.ArgumentParser(prog="demo_runner")
    sub = p.add_subparsers(dest="subcommand", required=True)
    sub.add_parser("list",
                   help="list demos and their enabled status").set_defaults(func=commands.cmd_list)
    sub.add_parser("build",
                   help="build in-tree demo guests").set_defaults(func=commands.cmd_build)
    sub.add_parser("qemu-args",
                   help="print the QEMU board-model flags").set_defaults(func=commands.cmd_qemu_args)
    # `type` resolves an ID or directory name to the demo name at parse time.
    demo_arg = dict(metavar="id|name", type=manifest.resolve_demo,
                    help="demo ID from `list` (e.g. 2) or directory name (e.g. 02_timer)")
    p_fetch = sub.add_parser("fetch", help="populate the external image cache for a demo")
    p_fetch.add_argument("name", nargs="?", **demo_arg)
    p_fetch.add_argument("--all", action="store_true",
                         help="fetch every enabled demo that has a fetch.sh")
    p_fetch.set_defaults(func=commands.cmd_fetch)
    p_run = sub.add_parser("run", help="launch a demo interactively")
    p_run.add_argument("name", **demo_arg)
    p_run.set_defaults(func=commands.cmd_run)
    p_ver = sub.add_parser("verify", help="run a demo and check manifest.expect")
    p_ver.add_argument("name", **demo_arg)
    p_ver.add_argument("--artifacts", metavar="DIR",
                       help="write bounded diagnostics for a failed run")
    p_ver.set_defaults(func=commands.cmd_verify)
    p_repeat = sub.add_parser("verify-repeat", help="repeat one demo and report its success rate")
    p_repeat.add_argument("name", **demo_arg)
    p_repeat.add_argument("--runs", type=int, required=True, choices=range(1, 101),
                          metavar="N", help="number of attempts (1..100)")
    p_repeat.add_argument("--summary", metavar="CSV",
                          help="write per-attempt status and elapsed time")
    p_repeat.add_argument("--artifacts", metavar="DIR",
                          help="write one bounded QEMU tail per failed attempt")
    p_repeat.set_defaults(func=commands.cmd_verify_repeat)
    p_all = sub.add_parser("verify-all", help="run all enabled demos")
    p_all.add_argument("--artifacts", metavar="DIR",
                       help="write bounded diagnostics for failed runs")
    p_all.set_defaults(func=commands.cmd_verify_all)
    p_dbg = sub.add_parser("debug", help="launch a demo with QEMU halted and GDB stub on :1234")
    p_dbg.add_argument("name", **demo_arg)
    p_dbg.set_defaults(func=commands.cmd_debug)
    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
