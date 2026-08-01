"""CLI adapter for local CI lanes."""

from __future__ import annotations

from ..services import ci


def _ci(args) -> int:
    return ci.run_lane(args.lane)


def register(subcommands) -> None:
    parser = subcommands.add_parser("ci", help="run a CI lane locally")
    parser.add_argument(
        "lane",
        choices=(*ci.BY_NAME, "all"),
        help="lane to run; all runs every lane in order",
    )
    parser.set_defaults(handler=_ci)
