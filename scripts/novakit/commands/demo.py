"""CLI adapter for demo catalog, execution, and verification."""

from __future__ import annotations

from pathlib import Path

from ..services import demo, manifest


def _list(_args) -> int:
    return demo.list_demos()


def _build(_args) -> int:
    return demo.build()


def _fetch(args) -> int:
    return demo.fetch(args.name, all_demos=args.all)


def _run(args) -> int:
    return demo.run(args.name, debug=args.debug)


def _verify(args) -> int:
    if args.all:
        return demo.verify_all(args.artifacts)
    return demo.verify_one(args.name, args.artifacts)


def _soak(args) -> int:
    return demo.soak(
        args.name,
        args.runs,
        summary=args.summary,
        artifact_dir=args.artifacts,
    )


def _scope(parser, demo_arg: dict) -> None:
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("name", nargs="?", **demo_arg)
    scope.add_argument(
        "--all",
        action="store_true",
        help="operate on every enabled demo",
    )


def register(subcommands) -> None:
    parser = subcommands.add_parser("demo", help="manage and verify demo guests")
    operations = parser.add_subparsers(
        dest="demo_command",
        required=True,
        title="operations",
    )

    operations.add_parser("list", help="list demos and their status").set_defaults(
        handler=_list
    )
    operations.add_parser("build", help="build in-tree guests").set_defaults(
        handler=_build
    )
    demo_arg = {
        "metavar": "DEMO",
        "type": manifest.resolve_demo,
        "help": "demo ID or directory name",
    }

    fetch = operations.add_parser("fetch", help="populate external image caches")
    _scope(fetch, demo_arg)
    fetch.set_defaults(handler=_fetch)

    run = operations.add_parser("run", help="launch one demo interactively")
    run.add_argument("name", **demo_arg)
    run.add_argument(
        "--debug",
        action="store_true",
        help="halt the CPU and expose a GDB server on port 1234",
    )
    run.set_defaults(handler=_run)

    verification = operations.add_parser(
        "verify",
        help="check expected output from one or every enabled demo",
    )
    _scope(verification, demo_arg)
    verification.add_argument(
        "--artifacts",
        type=Path,
        metavar="DIR",
        help="write failure artifacts under this directory",
    )
    verification.set_defaults(handler=_verify)

    soak = operations.add_parser("soak", help="repeat verification of one demo")
    soak.add_argument("name", **demo_arg)
    soak.add_argument(
        "--runs",
        type=int,
        required=True,
        choices=range(1, 101),
        metavar="N",
        help="number of verification attempts (1-100)",
    )
    soak.add_argument(
        "--summary",
        type=Path,
        metavar="CSV",
        help="write per-attempt results to a CSV file",
    )
    soak.add_argument(
        "--artifacts",
        type=Path,
        metavar="DIR",
        help="write failure artifacts under this directory",
    )
    soak.set_defaults(handler=_soak)
