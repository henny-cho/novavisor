"""Trusted Firmware-A profiles, packaging, and QEMU verification."""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import build, config, demo_build, process, qemu, report

TFA_REPOSITORY = "https://github.com/ARM-software/arm-trusted-firmware.git"


@dataclass(frozen=True)
class Profile:
    preset: str
    load_pa: int
    manifest_name: str


PROFILES = {
    "n1sdp": Profile(
        "aarch64-n1sdp-release",
        0x80000000,
        "n1sdp-payloads.yml",
    ),
    "qemu-tfa": Profile(
        "aarch64-qemu-tfa-release",
        0x50000000,
        "qemu-tfa-payloads.yml",
    ),
}


def tfa_source_dir() -> Path:
    version = config.tool_version("TFA_VERSION")
    return (
        config.REPO
        / "external"
        / "cache"
        / "firmware"
        / f"arm-trusted-firmware-{version}"
    )


def _revision(source: Path) -> str:
    result = process.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def prepare_tfa_source() -> Path:
    source = tfa_source_dir()
    commit = config.tool_version("TFA_COMMIT")
    if (source / ".git").is_dir() and _revision(source) == commit:
        return source

    source.parent.mkdir(parents=True, exist_ok=True)
    if not (source / ".git").is_dir():
        if source.exists() and any(source.iterdir()):
            raise SystemExit(f"TF-A cache is not a git checkout: {source}")
        process.run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                TFA_REPOSITORY,
                str(source),
            ]
        )
    process.run(
        ["git", "-C", str(source), "fetch", "--depth=1", "origin", commit]
    )
    process.run(["git", "-C", str(source), "checkout", "--detach", commit])
    if _revision(source) != commit:
        raise SystemExit("Trusted Firmware-A revision verification failed")
    return source


def _require_payload(path: Path) -> Path:
    payload = path.resolve()
    if not payload.is_file():
        raise SystemExit(f"BL33 payload not found: {payload}")
    if shutil.which(
        "aarch64-none-elf-gcc",
        path=config.command_env().get("PATH"),
    ) is None:
        raise SystemExit("aarch64-none-elf toolchain is not available")
    return payload


def _build_tfa(
    platform: str,
    payload: Path,
    output_dir: Path,
    *targets: str,
) -> Path:
    source = prepare_tfa_source()
    build_base = output_dir / "tf-a-build"
    platform_options = (
        ["QEMU_USE_GIC_DRIVER=QEMU_GICV3"]
        if platform == "qemu"
        else []
    )
    process.run(
        [
            "make",
            "-C",
            str(source),
            f"-j{os.cpu_count() or 2}",
            f"BUILD_BASE={build_base}",
            "CROSS_COMPILE=aarch64-none-elf-",
            f"PLAT={platform}",
            "DEBUG=0",
            f"BL33={payload}",
            *platform_options,
            *targets,
        ]
    )
    return build_base / platform / "release"


def build_profile(name: str) -> Path:
    profile = PROFILES[name]
    demo_build.build_demos()
    binary = config.DEMO_BUILD_DIR / "01_hello" / "hello.bin"
    record = demo_build.payload_record()(
        binary,
        guest=0,
        name="platform-smoke",
        load_pa=profile.load_pa,
        entry=0x50000000,
        memory_size=0x00100000,
    )
    manifest = config.BUILD_ROOT / profile.manifest_name
    demo_build.write_payload_manifest(manifest, [record])
    elf = build.build(
        build.BuildSpec(
            preset=profile.preset,
            config_path=config.REPO / "configs" / "platform-smoke.yml",
            payloads_path=manifest,
        )
    )
    binary = elf.with_suffix(".bin")
    if not binary.is_file():
        raise SystemExit(f"firmware profile did not produce {binary}")
    return binary


def package_qemu(payload: Path, output_dir: Path) -> Path:
    payload = _require_payload(payload)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    tfa_output = _build_tfa("qemu", payload, output_dir, "all", "fip")
    bl1 = tfa_output / "bl1.bin"
    fip = tfa_output / "fip.bin"
    if not bl1.is_file() or not fip.is_file():
        raise SystemExit("TF-A QEMU build did not produce BL1 and FIP")

    flash = output_dir / "flash.bin"
    with flash.open("wb") as image:
        image.write(bl1.read_bytes())
        image.seek(256 * 1024)
        image.write(fip.read_bytes())
    print(f"QEMU TF-A flash image: {flash}")
    return flash


def package_n1sdp(payload: Path, output_dir: Path) -> Path:
    payload = _require_payload(payload)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    tfa_output = _build_tfa("n1sdp", payload, output_dir, "fip")
    fip = tfa_output / "fip.bin"
    fiptool = tfa_output / "tools" / "fiptool" / "fiptool"
    process.run([str(fiptool), "update", "--nt-fw", str(payload), str(fip)])

    with tempfile.TemporaryDirectory() as directory:
        unpacked = Path(directory) / "bl33.bin"
        process.run(
            [
                str(fiptool),
                "unpack",
                "--force",
                "--nt-fw",
                str(unpacked),
                str(fip),
            ]
        )
        if payload.read_bytes() != unpacked.read_bytes():
            raise SystemExit("packaged BL33 does not match the linked image")

    package = output_dir / "fip.bin"
    if not package.exists() or package.read_bytes() != fip.read_bytes():
        shutil.copyfile(fip, package)
    print(f"N1SDP firmware package: {package}")
    return package


def verify_qemu_tfa(
    *,
    build_only: bool,
    payload: Path | None = None,
    output_dir: Path = config.BUILD_ROOT / "qemu-tfa-firmware",
) -> int:
    payload = build_profile("qemu-tfa") if payload is None else payload
    flash = package_qemu(payload, output_dir)
    if build_only:
        return 0

    command = qemu.board_command(bios=flash, secure=True)
    markers = (
        "BL31: v",
        "NovaVisor booted",
        "core 1 online",
        "Hello from EL1 guest",
        "demo_exit code=0",
    )
    expectations = [
        {"pattern": re.escape(marker), "within_seconds": 120}
        for marker in markers
    ]
    output_dir = output_dir.resolve()
    log_path = output_dir / "smoke.log"
    diagnostics = output_dir / "smoke.diagnostics.json"
    diagnostics.unlink(missing_ok=True)

    def matched(match: qemu.PatternMatch) -> None:
        marker = markers[match.index - 1]
        print(f"[nova firmware] matched[{match.index}/{len(markers)}] {marker}")

    try:
        with log_path.open("w", encoding="utf-8") as log:
            verification = qemu.run_command(
                command,
                expectations,
                120,
                stream=log,
                on_match=matched,
                fatal_patterns=config.FATAL_OUTPUT_PATTERNS,
            )
    except qemu.VerificationInterrupted as interrupted:
        report.report_verification_failure(
            interrupted.result,
            scope="nova firmware",
        )
        report.write_verification_diagnostics(
            diagnostics,
            "qemu-tfa",
            interrupted.result,
        )
        raise interrupted.cause.with_traceback(
            interrupted.cause.__traceback__
        ) from None

    if not verification.result.ok:
        report.report_verification_failure(
            verification.result,
            scope="nova firmware",
        )
        report.write_verification_diagnostics(
            diagnostics,
            "qemu-tfa",
            verification.result,
        )
        if verification.capture.tail:
            print("[nova firmware] --- QEMU output tail ---", file=sys.stderr)
            print(verification.capture.tail, file=sys.stderr)
        return 1

    print("[nova firmware] PASS: TF-A chain handoff contract verified")
    return 0


def _profile(args) -> int:
    build_profile(args.platform)
    return 0


def _package(args) -> int:
    package_n1sdp(
        args.payload,
        args.output or config.BUILD_ROOT / "n1sdp-firmware",
    )
    return 0


def _verify(args) -> int:
    return verify_qemu_tfa(
        build_only=args.build_only,
        payload=args.payload,
        output_dir=args.output or config.BUILD_ROOT / "qemu-tfa-firmware",
    )


def register(subcommands) -> None:
    parser = subcommands.add_parser("firmware", help="build and verify TF-A")
    operations = parser.add_subparsers(dest="firmware_command", required=True)

    profile = operations.add_parser("profile", help="build a BL33 profile")
    profile.add_argument("platform", choices=tuple(PROFILES))
    profile.set_defaults(handler=_profile)

    package = operations.add_parser("package", help="package board firmware")
    package.add_argument("platform", choices=("n1sdp",))
    package.add_argument("--payload", type=Path, required=True)
    package.add_argument("--output", type=Path)
    package.set_defaults(handler=_package)

    verify = operations.add_parser("verify", help="verify a firmware chain")
    verify.add_argument("platform", choices=("qemu-tfa",))
    verify.add_argument("--build-only", action="store_true")
    verify.add_argument("--output", type=Path)
    verify.add_argument("--payload", type=Path)
    verify.set_defaults(handler=_verify)
