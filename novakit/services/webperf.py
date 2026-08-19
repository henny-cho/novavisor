"""What the workbench UI costs, and whether it keeps up.

The measurement belongs to a browser and lives beside the page in
`web/perf`; this decides what to run it with, what the numbers mean, and
what to print. The split is the web gate's: node owns running
JavaScript, and the pinned toolchain in `web/` is the one place that says
which node and which browser.

A frame is the line. The bridge flushes on a fixed window and the reader
watches a screen, so what a scenario answers is not "how many
milliseconds" but "can this arrive as often as it really does and still
leave the frame". That is `core`: cost times the rate the bridge
publishes at, as a fraction of one core.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # noqa: TID251 — one probe that must answer rather than raise
import sys
from pathlib import Path

from ..core import config, proc

# Enough of a core that the UI is visibly behind for as long as it lasts.
BUSY_SHARE = 0.5


def _browser() -> Path | None:
    """Where the pinned Playwright keeps its browser, if it has one."""
    try:
        found = subprocess.run(
            ["node", "-e", "process.stdout.write(require('playwright').chromium.executablePath())"],
            cwd=config.WEB_DIR,
            env=config.command_env(),
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    path = Path(found.stdout.strip())
    return path if path.is_file() else None


def _toolchain() -> int:
    """The web toolchain, installed from its own lock as the gate does."""
    missing = [
        tool
        for tool in ("node", "npm")
        if shutil.which(tool, path=config.command_env()["PATH"]) is None
    ]
    if missing:
        print(f"[perf] missing tools: {', '.join(missing)}; run ./bootstrap", file=sys.stderr)
        return 1
    lock = config.WEB_DIR / "package-lock.json"
    installed = config.WEB_DIR / "node_modules" / ".package-lock.json"
    if not installed.is_file() or installed.stat().st_mtime < lock.stat().st_mtime:
        proc.run(["npm", "ci"], cwd=config.WEB_DIR)
    if _browser() is not None:
        return 0
    # A hundred megabytes is a fair price for a measurement someone asked
    # for and a poor one for every CI run, so it is asked for rather than
    # fetched, and no gate depends on it.
    here = config.WEB_DIR.relative_to(config.REPO)
    print(
        "[perf] the pinned Playwright has no browser. Install it once:\n"
        f"    (cd {here} && npx playwright install chromium)",
        file=sys.stderr,
    )
    return 1


def _table(document: dict) -> None:
    print(f"[perf] {document['browser']}, median of {document['samples']}")
    print(f"  {'scenario':<15}{'rate':>6}{'script':>9}{'render':>9}{'total':>9}{'core':>7}")
    for scenario in document["scenarios"]:
        when = "once" if scenario["once"] else f"{scenario['rate_hz']:g}Hz"
        share = scenario["share"]
        load = "     —" if share is None else f"{share:6.1%}"
        print(
            f"  {scenario['name']:<15}{when:>6}"
            f"{scenario['script_ms']:>9.2f}{scenario['render_ms']:>9.2f}"
            f"{scenario['total_ms']:>9.2f}{load}"
        )
        print(f"      {scenario['what']}")
    print(f"  a frame is {document['scenarios'][0]['budget_ms']:.2f}ms")


def _findings(document: dict) -> list[str]:
    """Two different complaints, and a third the browser makes itself.

    A batch over one frame is felt when it happens; a share over half a
    core is felt for as long as it keeps happening, which is the worse of
    the two and the reason the publish rate is carried this far.
    """
    said = []
    for scenario in document["scenarios"]:
        if not scenario["once"] and scenario["total_ms"] > scenario["budget_ms"]:
            said.append(f"{scenario['name']}: {scenario['total_ms']:.2f}ms is more than a frame")
        if (scenario["share"] or 0) > BUSY_SHARE:
            said.append(
                f"{scenario['name']}: {scenario['share']:.0%} of a core "
                f"at {scenario['rate_hz']:g}Hz"
            )
        if scenario["longtasks"]:
            said.append(
                f"{scenario['name']}: {scenario['longtasks']} long task(s), "
                f"worst {scenario['worst_longtask_ms']}ms"
            )
    said += [f"page fault: {fault}" for fault in document["faults"]]
    return said


def measure(samples: int, as_json: bool, check: bool) -> int:
    code = _toolchain()
    if code:
        return code

    finished = proc.run(
        ["node", "perf/measure.mjs", str(samples)],
        cwd=config.WEB_DIR,
        capture=True,
        check=False,
    )
    if not finished.stdout.strip():
        sys.stderr.write(finished.stderr)
        return finished.returncode or 1
    document = json.loads(finished.stdout)

    if as_json:
        print(json.dumps(document, indent=1))
    else:
        sys.stderr.write(finished.stderr)
        _table(document)
    findings = _findings(document)
    for finding in findings:
        print(f"[perf] {finding}", file=sys.stderr)
    if document["faults"]:
        return 1
    return 1 if check and findings else 0
