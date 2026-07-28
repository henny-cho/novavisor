"""Argument parsing and command dispatch for the public automation CLI."""

from __future__ import annotations

import argparse

from . import build, checks, demo, firmware


def _add_build_options(parser: argparse.ArgumentParser, *, allow_clean: bool = True) -> None:
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--preset")
    if allow_clean:
        parser.add_argument("--clean", action="store_true")
    else:
        parser.set_defaults(clean=False)
    parser.add_argument("--config")
    parser.add_argument("--payloads")


def _build(args) -> int:
    build.build(build.spec_from_args(args))
    return 0


def _clean(_args) -> int:
    build.clean()
    return 0


def _format(args) -> int:
    return checks.format_sources(check=args.check)


def _lint(_args) -> int:
    return checks.lint()


def _run(args) -> int:
    return build.run_hypervisor(build.spec_from_args(args), debug=args.debug)


def _inspect(args) -> int:
    return build.inspect_elf(build.spec_from_args(args), args.operation)


def _test(_args) -> int:
    return checks.test()


def _ci(args) -> int:
    return checks.ci(args.lane)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="nova")
    sub = root.add_subparsers(dest="command", required=True)

    build_parser = sub.add_parser("build", help="configure and build a CMake preset")
    _add_build_options(build_parser)
    build_parser.set_defaults(handler=_build)

    clean_parser = sub.add_parser("clean", help="remove all build outputs")
    clean_parser.set_defaults(handler=_clean)

    format_parser = sub.add_parser(
        "fmt",
        help="format C and C++ sources",
    )
    format_parser.add_argument("--check", action="store_true")
    format_parser.set_defaults(handler=_format)

    lint_parser = sub.add_parser("lint", help="run target clang-tidy")
    lint_parser.set_defaults(handler=_lint)

    run_parser = sub.add_parser("run", help="build and launch the hypervisor")
    _add_build_options(run_parser)
    run_parser.add_argument("--debug", action="store_true")
    run_parser.set_defaults(handler=_run)

    for operation in ("size", "objdump"):
        inspect_parser = sub.add_parser(operation, help=f"inspect the target ELF with {operation}")
        _add_build_options(inspect_parser, allow_clean=False)
        inspect_parser.set_defaults(handler=_inspect, operation=operation)

    test_parser = sub.add_parser("test", help="run host and script tests")
    test_parser.set_defaults(handler=_test)

    demo.register(sub)
    firmware.register(sub)

    ci_parser = sub.add_parser("ci", help="run a local CI lane")
    ci_parser.add_argument("lane", choices=(*checks.CI_LANES, "all"))
    ci_parser.set_defaults(handler=_ci)

    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.handler(args)
