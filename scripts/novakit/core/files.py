"""Repository file discovery through the index git already maintains.

Walking the tree to answer "which files are ours" reads every build
artifact and cache on the way, and needs an exclusion list that drifts.
git already knows, so ask it.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import config, proc

SHEBANG = re.compile(r"^#!.*\b(?:ba|z)?sh\b")


def tracked(*suffixes: str) -> list[Path]:
    """Repository files, or only those with one of the given suffixes.

    Untracked-but-not-ignored files count: a source added this minute still
    has to be formatted and linted before it can be committed.
    """
    # A CI container mounts the checkout from another uid, so name the repo
    # trusted here instead of depending on how it was cloned.
    result = proc.run(
        [
            "git",
            "-C",
            str(config.REPO),
            "-c",
            f"safe.directory={config.REPO}",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"git ls-files failed: {result.stderr.strip()}")
    paths = (config.REPO / name for name in result.stdout.split("\0") if name)
    return sorted(
        path
        for path in paths
        if path.is_file() and (not suffixes or path.suffix in suffixes)
    )


def shell_scripts() -> list[Path]:
    """Tracked shell sources: a .sh suffix, or an executable with a sh shebang."""
    scripts = []
    for path in tracked():
        if path.suffix == ".sh":
            scripts.append(path)
        elif not path.suffix and path.stat().st_mode & 0o111:
            # Only extension-less executables can hide a shebang, so this
            # is the only case that has to read anything.
            first_line = path.read_text(errors="ignore").split("\n", 1)[0]
            if SHEBANG.match(first_line):
                scripts.append(path)
    return scripts
