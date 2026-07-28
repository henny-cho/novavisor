"""GitHub Actions: the one place that knows a run is inside a workflow.

Three modules used to ask the environment this themselves and two of them
owned a copy of the step-summary append. What "we are in CI" means, and where
a summary block goes, is one fact.
"""

from __future__ import annotations

import os


def in_actions() -> bool:
    """True inside a workflow run, where the uploaded log is the record."""
    return os.environ.get("GITHUB_ACTIONS") == "true"


def step_summary(*lines: str) -> None:
    """Append one block to the job summary. No-op outside Actions."""
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
