"""CLI adapter for firmware build, packaging, and verification."""

from __future__ import annotations

from pathlib import Path

from ..services import tfa


def _build(args) -> int:
    tfa.build_profile(args.platform)
    return 0


def _package(args) -> int:
    tfa.package_platform(
        args.platform,
        args.payload,
        args.output,
    )
    return 0


def _verify(args) -> int:
    return tfa.verify_platform(
        args.platform,
        build_only=args.build_only,
        payload=args.payload,
        output_dir=args.output,
    )


def register(subcommands) -> None:
    parser = subcommands.add_parser("firmware", help="build and verify TF-A firmware")
    operations = parser.add_subparsers(
        dest="firmware_command",
        required=True,
        title="operations",
    )

    build = operations.add_parser("build", help="build a BL33 firmware profile")
    build.add_argument(
        "platform",
        choices=tuple(tfa.PROFILES),
        help="profile to build",
    )
    build.set_defaults(handler=_build)

    package = operations.add_parser("package", help="package a BL33 firmware image")
    package.add_argument(
        "platform",
        choices=tuple(tfa.PACKAGERS),
        help="target platform",
    )
    package.add_argument(
        "--payload",
        type=Path,
        required=True,
        metavar="FILE",
        help="BL33 payload to package",
    )
    package.add_argument(
        "--output",
        type=Path,
        metavar="DIR",
        help="firmware output directory",
    )
    package.set_defaults(handler=_package)

    chain = operations.add_parser("verify", help="verify a firmware chain")
    chain.add_argument(
        "platform",
        choices=tuple(tfa.VERIFIERS),
        help="firmware platform to verify",
    )
    chain.add_argument(
        "--build-only",
        action="store_true",
        help="build the chain without launching QEMU",
    )
    chain.add_argument(
        "--output",
        type=Path,
        metavar="DIR",
        help="firmware output directory",
    )
    chain.add_argument(
        "--payload",
        type=Path,
        metavar="FILE",
        help="use an existing BL33 payload",
    )
    chain.set_defaults(handler=_verify)
