"""Trusted Firmware-A: the pinned source, the build, the images, the chain.

Two consumers verify a chain — the `firmware verify` command and the CI
runtime lane — so the handoff a real BL1 -> BL2 -> BL31 -> BL33 must show
lives here with the images it boots.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..core import board, config, proc
from ..image.payload import make_record
from . import artifacts, cmake, expect, verify

REPOSITORY = "https://github.com/ARM-software/arm-trusted-firmware.git"


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


def source_dir() -> Path:
    version = config.tool_version("TFA_VERSION")
    return (
        config.REPO
        / "external"
        / "cache"
        / "firmware"
        / f"arm-trusted-firmware-{version}"
    )


def _revision(source: Path) -> str:
    result = proc.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def prepare_source() -> Path:
    source = source_dir()
    commit = config.tool_version("TFA_COMMIT")
    if (source / ".git").is_dir() and _revision(source) == commit:
        return source

    source.parent.mkdir(parents=True, exist_ok=True)
    if not (source / ".git").is_dir():
        if source.exists() and any(source.iterdir()):
            raise SystemExit(f"TF-A cache is not a git checkout: {source}")
        proc.run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                REPOSITORY,
                str(source),
            ]
        )
    proc.run(["git", "-C", str(source), "fetch", "--depth=1", "origin", commit])
    proc.run(["git", "-C", str(source), "checkout", "--detach", commit])
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
    source = prepare_source()
    build_base = output_dir / "tf-a-build"
    platform_options = (
        ["QEMU_USE_GIC_DRIVER=QEMU_GICV3"]
        if platform == "qemu"
        else []
    )
    proc.run(
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
    """Link the BL33 payload a profile boots, and return its flat binary."""
    profile = PROFILES[name]
    artifacts.build_demos()
    record = make_record(
        config.DEMO_BUILD_DIR / "01_hello" / "hello.bin",
        guest=0,
        name="platform-smoke",
        load_pa=profile.load_pa,
        entry=0x50000000,
        memory_size=0x00100000,
    )
    payload_manifest = config.BUILD_ROOT / profile.manifest_name
    artifacts.write_payload_manifest(payload_manifest, [record])
    elf = cmake.build(cmake.BuildSpec.of(
        preset=profile.preset,
        config_path=config.REPO / "configs" / "platform-smoke.yml",
        payloads_path=payload_manifest,
    ))
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
    proc.run([str(fiptool), "update", "--nt-fw", str(payload), str(fip)])

    with tempfile.TemporaryDirectory() as directory:
        unpacked = Path(directory) / "bl33.bin"
        proc.run(
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
    payload = build_profile("qemu-tfa") if payload is None else payload
    flash = package_qemu(payload, output)
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
