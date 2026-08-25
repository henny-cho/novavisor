"""The demo suite: which demos are enabled, and how one or all get verified.

The `demo` command and the CI runtime lane both drive this, so it sits below
both. Per-run artifact composition stays in artifacts.py; what a manifest
promises stays in manifest.py. This owns only the set and the loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..core import actions, config, proc
from . import artifacts, cmake, manifest, report, verify

SCOPE = "nova demo"


def sink(tail: Path | None = None) -> verify.Sink:
    # In Actions the uploaded log is the record, so streaming would double
    # every line in the job output; locally the live output is the point.
    return verify.Sink(
        stream=None if actions.in_actions() else sys.stdout,
        tail=tail,
    )


def verify_one(name: str, paths: report.ArtifactPaths | None = None) -> int:
    _, demo_manifest = manifest.load_manifest(name)
    if not demo_manifest.get("enabled", False):
        print(f"[{SCOPE}] SKIP {name} (manifest.enabled=false)")
        return 0

    # A manifest is either a single run (top-level config/expect) or a
    # `variants:` list — one full run (build + QEMU + expect) each, with
    # the shared guests list. demo/11_configurable uses this to verify
    # the same guest under two configs.
    for index, variant in enumerate(manifest.manifest_variants(demo_manifest), start=1):
        scenario = artifacts.scenario_for(name, demo_manifest, variant)
        tail = None if paths is None else paths.verify_tail(name, index)
        rc = verify.run_scenario(scenario, sink(tail), scope=SCOPE)
        if rc != 0:
            return rc
    return 0


def verify_all(paths: report.ArtifactPaths | None = None) -> int:
    enabled = [(n, m) for n, m in manifest.iter_demos() if m.get("enabled", False)]
    if not enabled:
        print(f"[{SCOPE}] no enabled demos; nothing to verify.")
        return 0

    # Warm the shared artifacts once so each demo's own build stays incremental.
    cmake.build(cmake.BuildSpec.of(preset=config.HV_PRESET))
    artifacts.build_demos()

    failures = [name for name, _ in enabled if verify_one(name, paths) != 0]
    if failures:
        print(f"\n[{SCOPE}] {len(failures)} demo(s) failed: "
              f"{', '.join(failures)}", file=sys.stderr)
        return 1
    print(f"\n[{SCOPE}] all {len(enabled)} demo(s) passed.")
    return 0


def fetch_all() -> int:
    names = [
        name
        for name, demo_manifest in manifest.iter_demos()
        if demo_manifest.get("enabled", False)
        and (config.DEMO_DIR / name / "fetch.sh").exists()
    ]
    for name in names:
        result = proc.call(["bash", str(config.DEMO_DIR / name / "fetch.sh")])
        if result != 0:
            return result
    print(f"[{SCOPE}] fetched {len(names)} demo image set(s).")
    return 0
