"""Firmware commands: link a BL33 profile, package a board image, verify a chain."""

from __future__ import annotations

from pathlib import Path

from ..core import config
from ..services import tfa


def _profile(args) -> int:
    tfa.build_profile(args.platform)
    return 0


def _package(args) -> int:
    tfa.package_n1sdp(
        args.payload,
        args.output or config.BUILD_ROOT / "n1sdp-firmware",
    )
    return 0


def _verify(args) -> int:
    return tfa.verify_chain(
        build_only=args.build_only,
        payload=args.payload,
        output_dir=args.output,
    )


def register(subcommands) -> None:
    parser = subcommands.add_parser("firmware", help="build and verify TF-A")
    operations = parser.add_subparsers(dest="firmware_command", required=True)

    profile = operations.add_parser("profile", help="build a BL33 profile")
    profile.add_argument("platform", choices=tuple(tfa.PROFILES))
    profile.set_defaults(handler=_profile)

    package = operations.add_parser("package", help="package board firmware")
    package.add_argument("platform", choices=("n1sdp",))
    package.add_argument("--payload", type=Path, required=True)
    package.add_argument("--output", type=Path)
    package.set_defaults(handler=_package)

    chain = operations.add_parser("verify", help="verify a firmware chain")
    chain.add_argument("platform", choices=("qemu-tfa",))
    chain.add_argument("--build-only", action="store_true")
    chain.add_argument("--output", type=Path)
    chain.add_argument("--payload", type=Path)
    chain.set_defaults(handler=_verify)
