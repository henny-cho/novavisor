"""Firmware commands: link a BL33 profile, package a board image, verify a chain."""

from __future__ import annotations

from pathlib import Path

from ..core import board, config
from ..services import expect, tfa, verify

# What a real BL1 -> BL2 -> BL31 -> BL33 handoff must print, in order: the
# firmware banner, then the hypervisor reaching the same guest exit the
# -kernel path reaches.
CHAIN_MARKERS = (
    "BL31: v",
    "NovaVisor booted",
    "core 1 online",
    "Hello from EL1 guest",
    "demo_exit code=0",
)
CHAIN_TIMEOUT = 120


def _chain_scenario(flash: Path) -> expect.Scenario:
    return expect.Scenario(
        label="qemu-tfa chain handoff",
        phase="firmware",
        command=tuple(board.command(bios=flash, secure=True)),
        timeout_seconds=CHAIN_TIMEOUT,
        expectations=tuple(
            {"pattern": marker, "within_seconds": CHAIN_TIMEOUT}
            for marker in CHAIN_MARKERS
        ),
    )


def verify_chain(
    *,
    build_only: bool,
    payload: Path | None = None,
    output_dir: Path | None = None,
) -> int:
    output = (output_dir or config.BUILD_ROOT / "qemu-tfa-firmware").resolve()
    payload = tfa.build_profile("qemu-tfa") if payload is None else payload
    flash = tfa.package_qemu(payload, output)
    if build_only:
        return 0

    diagnostics = output / "smoke.diagnostics.json"
    diagnostics.unlink(missing_ok=True)
    with (output / "smoke.log").open("w", encoding="utf-8") as log:
        return verify.run_scenario(
            _chain_scenario(flash),
            verify.Sink(stream=log, diagnostics=diagnostics),
            scope="nova firmware",
        )


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
    return verify_chain(
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

    verify_chain_parser = operations.add_parser("verify", help="verify a firmware chain")
    verify_chain_parser.add_argument("platform", choices=("qemu-tfa",))
    verify_chain_parser.add_argument("--build-only", action="store_true")
    verify_chain_parser.add_argument("--output", type=Path)
    verify_chain_parser.add_argument("--payload", type=Path)
    verify_chain_parser.set_defaults(handler=_verify)
