"""CI lanes: ownership, execution order, timing, and failure evidence."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..core import actions, config, cpu_profiles, proc
from ..image import observe
from . import cmake, firmperf, gates, report, suite, tfa
from .workbench import checks

RUNTIME_PRESETS = (
    "aarch64-release",
    "aarch64-minimal-release",
    "aarch64-standard-release",
    # The two CPU pairings a QEMU run can prove. In this tuple and not a
    # separate one, so their images get the same build, structure and ELF
    # analysis as every other shipped image rather than a narrower check.
    "aarch64-a57-on-n1-release",
    "aarch64-n1-on-n1-release",
)
# Where a failing lane may have left something worth keeping: every tree
# it builds, plus the firmware profile's.
EVIDENCE_PRESETS = (config.HV_PRESET, *RUNTIME_PRESETS, "aarch64-n1sdp-release")
EVIDENCE = report.ArtifactPaths(config.BUILD_ROOT / "ci-evidence")


@dataclass(frozen=True)
class Lane:
    name: str
    steps: tuple[tuple[str, Callable[[], int]], ...]
    need_guests: bool = False
    need_firmware: bool = False
    cache_scope: str = "target"
    timeout_minutes: int = 15

    def __post_init__(self) -> None:
        # Step names key the job summary, so a repeat would report two
        # outcomes under one heading. Refuse the table, not the report.
        names = [step for step, _ in self.steps]
        if len(names) != len(set(names)):
            raise ValueError(f"lane {self.name}: duplicate step name")


# Keep these adapters late-bound so tests and callers can replace a service
# operation without rebuilding the immutable lane table.
def _format() -> int:
    return gates.format_sources(check=True)


def _tests() -> int:
    return gates.test()


def _static() -> int:
    return gates.static_analysis()


def _manifest() -> int:
    # Runs after static-analysis, whose lint pass built the debug ELF.
    return checks.verify_manifest()


def _presets() -> int:
    for preset in RUNTIME_PRESETS:
        cmake.build(cmake.BuildSpec.of(preset=preset))
    return 0


# What each CPU pairing must have compiled to. Two runs of one demo
# would pass just as happily on two identical binaries, so the flag is
# read back before the runs are believed.
CPU_PAIRINGS = (
    ("aarch64-a57-on-n1-release", "cortex-a57"),
    ("aarch64-n1-on-n1-release", "neoverse-n1"),
)


def _cpu_profiles() -> int:
    """That the pairings differ where they claim to, and that a bad one is refused.

    The demos step runs 01_hello on both N1 pairings through their own
    variants. This checks the premise those runs rest on — the images
    really were built for different cores — and the negative the runs
    cannot show, because a refused pairing never reaches QEMU.
    """
    for preset, expected in CPU_PAIRINGS:
        elf = cmake.preset_dir(preset) / "novavisor.elf"
        view = observe.view_of(elf)
        built = cpu_profiles.build_profile(view.build_cpu)
        if built.mcpu != expected:
            raise SystemExit(
                f"[ci] {preset} was built for {built.mcpu}, expected {expected}")
        cpu_profiles.require_compatible(built, cpu_profiles.runtime_profile(view.runtime_cpu))
        print(f"[ci] {preset}: -mcpu={built.mcpu} on {view.runtime_cpu}")

    # N1 codegen on a baseline core: refused at configure, so QEMU is
    # never handed a machine that would fault on an unknown instruction.
    refused = proc.run(
        [
            "cmake", "-S", str(config.REPO), "-B", str(config.BUILD_ROOT / "cpu-negative"),
            f"-DCMAKE_TOOLCHAIN_FILE={config.REPO / 'cmake' / 'toolchain-aarch64.cmake'}",
            "-DNOVA_ARCH=aarch64", "-DNOVA_BOARD=qemu_virt", "-DNOVA_PROJECT=qemu_virt_arm64",
            "-DNOVA_CPU_BUILD_PROFILE=n1-tuned", "-DNOVA_CPU_RUNTIME_PROFILE=a57",
        ],
        capture=True, check=False,
    )
    if refused.returncode == 0 or "neoverse-n1-codegen" not in (refused.stdout + refused.stderr):
        raise SystemExit("[ci] an N1 build on a baseline core was not refused by name")
    print("[ci] n1-tuned on a57: refused at configure")
    return 0


def _structure() -> int:
    # After presets: the analyser reads the images that step builds, and
    # its suite skips wherever they are absent.
    return firmperf.gate()


# The demo one measurement is taken of. Short and single-guest, because
# what this step proves is that a machine writes what the report reads —
# not anything about the workload, which the demos step already runs.
MEASURED_DEMO = "01_hello"


def _measurement() -> int:
    """One real run through the whole report, and the same answer twice.

    The unit tests build recordings by hand, so they can only show the
    rules hold for what they wrote. Whether a machine actually records
    what those rules assume needs a machine, which is this lane.
    """
    preset = firmperf.demo_preset(MEASURED_DEMO)
    into = cmake.preset_dir(preset) / "measurements" / MEASURED_DEMO
    shutil.rmtree(into, ignore_errors=True)  # the lane's own scratch
    firmperf.measure(MEASURED_DEMO, runs=2)
    firmperf.structure(preset, recorded=into)
    if firmperf.report(preset, recorded=into) != firmperf.report(preset, recorded=into):
        raise SystemExit("[ci] the same recording reported differently twice")
    return 0


def _firmware_chain() -> int:
    tfa.build_profile("n1sdp")
    return tfa.verify_chain(build_only=False)


def _demos() -> int:
    if suite.fetch_all() != 0:
        return 1
    return suite.verify_all(EVIDENCE)


LANES = (
    Lane("host", (("format", _format), ("tests", _tests)), cache_scope="host", timeout_minutes=15),
    Lane("static", (("static-analysis", _static), ("manifest", _manifest)), cache_scope="target", timeout_minutes=20),
    Lane(
        "runtime",
        (
            ("presets", _presets),
            ("cpu-profiles", _cpu_profiles),
            ("structure", _structure),
            ("measurement", _measurement),
            ("firmware", _firmware_chain),
            ("demos", _demos),
        ),
        need_guests=True,
        need_firmware=True,
        cache_scope="target",
        timeout_minutes=40,
    ),
)
BY_NAME = {lane.name: lane for lane in LANES}

SOAK_LANES = {
    "soak-dma": Lane("soak-dma", (), need_guests=False, need_firmware=False, cache_scope="target", timeout_minutes=60),
    "soak-mixed": Lane("soak-mixed", (), need_guests=True, need_firmware=False, cache_scope="target", timeout_minutes=60),
}
ALL_METADATA_LANES = {**BY_NAME, **SOAK_LANES}


def lane_metadata(name: str) -> dict[str, str]:
    """Provide the single source of truth for CI workflow cache and environment parameters."""
    if name not in ALL_METADATA_LANES:
        raise ValueError(f"unknown lane: {name}")
    lane = ALL_METADATA_LANES[name]
    versions = config.tool_versions()
    return {
        "guests": "true" if lane.need_guests else "false",
        "firmware": "true" if lane.need_firmware else "false",
        "cache_scope": lane.cache_scope,
        "timeout_minutes": str(lane.timeout_minutes),
        "firmware_pin": versions.get("TFA_COMMIT", ""),
        "compiler": f"{versions.get('ARM_GNU_VERSION', '')}-tidy{versions.get('CLANG_TIDY_VERSION', '')}",
    }


def _ccache_stats() -> tuple[str, ...]:
    """Return the summary's compiler-cache block when ccache is available."""
    ccache = shutil.which("ccache", path=config.command_env().get("PATH"))
    if ccache is None:
        return ()
    stats = proc.run([ccache, "--show-stats"], capture=True, check=False)
    if stats.returncode != 0:
        return ()
    return (
        "<details><summary>ccache</summary>",
        "",
        "```text",
        stats.stdout.rstrip("\n"),
        "```",
        "</details>",
    )


def _append_summary(lane: str, steps: list[tuple[str, str, float]]) -> None:
    actions.step_summary(
        f"## nova ci {lane}",
        "",
        "| Step | Result | Seconds |",
        "| --- | --- | ---: |",
        *(f"| {name} | {status} | {elapsed:.1f} |" for name, status, elapsed in steps),
        "",
        *_ccache_stats(),
    )


def run_lane(lane: str) -> int:
    """Run one lane (or every lane), timing each step and reporting the table."""
    steps: list[tuple[str, str, float]] = []
    EVIDENCE.initialize()

    def run_step(name: str, action: Callable[[], int]) -> None:
        started = time.monotonic()
        status = "pass"
        try:
            if action() != 0:
                raise SystemExit(f"CI step failed: {name}")
        except BaseException:
            status = "fail"
            raise
        finally:
            steps.append((name, status, time.monotonic() - started))

    selected = LANES if lane == "all" else (BY_NAME[lane],)
    try:
        for entry in selected:
            for name, action in entry.steps:
                run_step(f"{entry.name}/{name}", action)
    finally:
        if any(status == "fail" for _, status, _ in steps):
            report.collect_lane_evidence(EVIDENCE, EVIDENCE_PRESETS)
        _append_summary(lane, steps)
    return 0
