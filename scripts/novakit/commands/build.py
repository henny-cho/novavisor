"""Build commands: configure, build, clean, launch, and inspect the image."""

from __future__ import annotations

from ..core import board, proc
from ..services import cmake

INSPECTORS = {
    "size": lambda elf: ["aarch64-none-elf-size", str(elf)],
    "objdump": lambda elf: ["aarch64-none-elf-objdump", "-d", "-S", "-C", str(elf)],
}


def add_options(parser, *, allow_clean: bool = True) -> None:
    """The preset selection every build-backed command shares."""
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--preset")
    parser.add_argument("--config")
    parser.add_argument("--payloads")
    if allow_clean:
        parser.add_argument("--clean", action="store_true")


def spec_from(args) -> cmake.BuildSpec:
    return cmake.BuildSpec.of(
        preset=args.preset,
        release=args.release,
        config_path=args.config,
        payloads_path=args.payloads,
        clean=getattr(args, "clean", False),
    )


def _build(args) -> int:
    cmake.build(spec_from(args))
    return 0


def _clean(_args) -> int:
    cmake.clean()
    return 0


def _run(args) -> int:
    elf = cmake.resolve_elf(spec_from(args), rebuild=True)
    command = board.command(kernel=elf)
    if args.debug:
        command += ["-s", "-S"]
        print("==> Launching QEMU with GDB stub on :1234 (CPU halted).")
        print("==> Press Ctrl-A then x in QEMU to exit.")
    return proc.call(command)


def _inspect(args) -> int:
    elf = cmake.resolve_elf(spec_from(args), rebuild=False)
    return proc.call(INSPECTORS[args.command](elf))


def register(subcommands) -> None:
    build = subcommands.add_parser("build", help="configure and build a CMake preset")
    add_options(build)
    build.set_defaults(handler=_build)

    subcommands.add_parser("clean", help="remove the build tree").set_defaults(
        handler=_clean
    )

    run = subcommands.add_parser("run", help="run the hypervisor under QEMU")
    add_options(run, allow_clean=False)
    run.add_argument("--debug", action="store_true")
    run.set_defaults(handler=_run)

    for name, help_text in (
        ("size", "report section sizes"),
        ("objdump", "disassemble the image"),
    ):
        inspect = subcommands.add_parser(name, help=help_text)
        add_options(inspect, allow_clean=False)
        inspect.set_defaults(handler=_inspect)
