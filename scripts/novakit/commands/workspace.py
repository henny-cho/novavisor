"""Primary workspace commands: build, run, clean, and inspect."""

from __future__ import annotations

from pathlib import Path

from ..services import cmake, workspace


def add_build_options(parser, *, allow_clean: bool = True) -> None:
    """Register the build inputs shared by workspace operations."""
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument(
        "--release",
        action="store_true",
        help="use the aarch64-release preset",
    )
    selector.add_argument(
        "--preset",
        metavar="NAME",
        help="use an explicit CMake preset",
    )
    parser.add_argument(
        "--config",
        type=Path,
        metavar="FILE",
        help="use a guest configuration instead of configs/default.yml",
    )
    parser.add_argument(
        "--payloads",
        type=Path,
        metavar="FILE",
        help="use a payload manifest instead of configs/payloads.yml",
    )
    if allow_clean:
        parser.add_argument(
            "--clean",
            action="store_true",
            help="remove the build tree before building",
        )


def spec_from(args) -> cmake.BuildSpec:
    return cmake.BuildSpec.of(
        preset=args.preset,
        release=args.release,
        config_path=args.config,
        payloads_path=args.payloads,
        clean=getattr(args, "clean", False),
    )


def _build(args) -> int:
    return workspace.build(spec_from(args))


def _clean(_args) -> int:
    return workspace.clean()


def _run(args) -> int:
    return workspace.run(spec_from(args), debug=args.debug)


def _inspect(args) -> int:
    return workspace.inspect(spec_from(args), args.inspect_command)


def register(subcommands) -> None:
    build = subcommands.add_parser("build", help="build the hypervisor")
    add_build_options(build)
    build.set_defaults(handler=_build)

    run = subcommands.add_parser("run", help="run the hypervisor under QEMU")
    add_build_options(run, allow_clean=False)
    run.add_argument(
        "--debug",
        action="store_true",
        help="halt the CPU and expose a GDB server on port 1234",
    )
    run.set_defaults(handler=_run)

    subcommands.add_parser("clean", help="remove the build tree").set_defaults(
        handler=_clean
    )

    inspect = subcommands.add_parser("inspect", help="inspect the hypervisor image")
    operations = inspect.add_subparsers(
        dest="inspect_command",
        required=True,
        title="operations",
    )
    for name, help_text in (
        ("size", "report section sizes"),
        ("disassemble", "disassemble the image with source lines"),
    ):
        operation = operations.add_parser(name, help=help_text)
        add_build_options(operation, allow_clean=False)
        operation.set_defaults(handler=_inspect)
