"""Demo manifest discovery, addressing, and schema validation.

Reading a manifest.yml and deciding whether its fields are admissible are the
same concern, so both live here; callers receive plain dicts.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from . import config
from .image import abi


def _require_yaml():
    # Pure verifier tests do not parse manifests. Load this optional runtime
    # dependency only for commands that need it.
    try:
        import yaml
        return yaml
    except ImportError:
        sys.exit(
            "nova demo: missing PyYAML. Install python3-yaml or PyYAML."
        )


def load_manifest(name: str) -> tuple[Path, dict]:
    yaml = _require_yaml()
    manifest_path = config.DEMO_DIR / name / "manifest.yml"
    if not manifest_path.exists():
        sys.exit(f"nova demo: no manifest at {manifest_path}")
    with open(manifest_path) as f:
        data = yaml.safe_load(f)
    return manifest_path, data


def iter_demos() -> list[tuple[str, dict]]:
    yaml = _require_yaml()
    out = []
    for p in sorted(config.DEMO_DIR.iterdir()):
        mf = p / "manifest.yml"
        if p.is_dir() and mf.exists():
            with open(mf) as f:
                out.append((p.name, yaml.safe_load(f)))
    return out


def demo_id(name: str) -> str:
    # The demo's ID is its directory's numeric NN_ prefix ("02_timer" → "02").
    prefix = name.split("_", 1)[0]
    return prefix if prefix.isdigit() else "-"


def resolve_demo(token: str) -> str:
    """Map a numeric ID ("2", "02") or a full directory name to the demo name."""
    names = [n for n, _ in iter_demos()]
    if token in names:
        return token
    if token.isdigit():
        matches = [n for n in names if demo_id(n) != "-" and int(demo_id(n)) == int(token)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            sys.exit(f"nova demo: ID '{token}' is ambiguous: {', '.join(matches)}")
    available = ", ".join(f"{demo_id(n)}={n}" for n in names) or "(none)"
    sys.exit(f"nova demo: unknown demo '{token}'. Available: {available}")


def manifest_config(manifest: dict) -> str | None:
    # For run/debug (no variant loop): the top-level config, or the
    # first variant's — matching what verify() exercises first.
    variants = manifest.get("variants")
    if variants:
        return variants[0].get("config", manifest.get("config"))
    return manifest.get("config")


def manifest_variants(manifest: dict) -> list[dict]:
    variants = manifest.get("variants")
    if variants is not None:
        return variants
    return [{
        "config": manifest.get("config"),
        "expect": manifest.get("expect", []),
    }]


def manifest_pattern_list(manifest: dict, key: str) -> tuple[str, ...]:
    patterns = manifest.get(key, [])
    if not isinstance(patterns, list) or any(not isinstance(pattern, str) or not pattern for pattern in patterns):
        raise SystemExit(f"[nova demo] manifest '{key}' must be a list of non-empty patterns")
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SystemExit(
                f"[nova demo] manifest '{key}' has invalid pattern /{pattern}/: {exc}"
            ) from exc
    return tuple(patterns)


def payload_mode(manifest: dict) -> str:
    mode = manifest.get("payload_mode", "loader")
    if mode not in ("loader", "embedded"):
        raise SystemExit("[nova demo] payload_mode must be 'loader' or 'embedded'")
    return mode


def validate(demo_name: str, manifest: dict) -> None:
    """Reject manifests the board model or the guest ABI cannot honour."""
    devices = manifest.get("qemu_devices", [])
    if not isinstance(devices, list) or not all(isinstance(device, str) and device for device in devices):
        raise SystemExit(
            f"[nova demo] {demo_name}: qemu_devices must be a list of non-empty strings"
        )
    for guest in manifest.get("guests", []):
        abi.validate_guest(
            f"[nova demo] {demo_name}: guest '{guest.get('name')}'", guest
        )
