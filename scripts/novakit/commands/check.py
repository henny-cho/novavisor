"""Quality gate commands: formatting, static analysis, and the test suites."""

from __future__ import annotations

from ..services import gates


def _format(args) -> int:
    return gates.format_sources(check=args.check)


def _lint(_args) -> int:
    return gates.lint()


def _test(_args) -> int:
    return gates.test()


def register(subcommands) -> None:
    formatting = subcommands.add_parser("fmt", help="format sources with clang-format")
    formatting.add_argument("--check", action="store_true")
    formatting.set_defaults(handler=_format)

    subcommands.add_parser("lint", help="run clang-tidy").set_defaults(handler=_lint)
    subcommands.add_parser("test", help="build and run host tests").set_defaults(
        handler=_test
    )
