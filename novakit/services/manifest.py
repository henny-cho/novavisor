"""Demo manifest discovery, addressing, and schema validation.

Reading a manifest.yml and deciding whether its fields are admissible are the
same concern, so both live here; callers receive plain dicts.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from ..core import config
from ..image import abi


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


def demo_names() -> list[str]:
    return [
        path.name
        for path in sorted(config.DEMO_DIR.iterdir())
        if path.is_dir() and (path / "manifest.yml").is_file()
    ]


def iter_demos() -> list[tuple[str, dict]]:
    yaml = _require_yaml()
    out = []
    for name in demo_names():
        with open(config.DEMO_DIR / name / "manifest.yml") as manifest_file:
            out.append((name, yaml.safe_load(manifest_file)))
    return out


def demo_id(name: str) -> str:
    # The demo's ID is its directory's numeric NN_ prefix ("02_timer" → "02").
    prefix = name.split("_", 1)[0]
    return prefix if prefix.isdigit() else "-"


def resolve_demo(token: str) -> str:
    """Map a numeric ID ("2", "02") or a full directory name to the demo name."""
    names = demo_names()
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


def variant_preset(variant: dict) -> str:
    """The composition one run is built on.

    Which components a run carries is as much a property of the variant
    as which guest table it boots — a demo that must pass on more than
    one composition says so here, rather than in a list of demo names
    kept beside a CI lane.
    """
    return variant.get("preset") or config.HV_PRESET


def manifest_preset(manifest: dict) -> str:
    # For run/debug (no variant loop): the first variant's, like config.
    return variant_preset(manifest_variants(manifest)[0])


def manifest_devices(manifest: dict, variant: dict) -> list[str]:
    """The QEMU devices one variant runs with.

    Overridden the way `config` is: what hardware a run has is as much
    a property of the variant as which guest table it boots, and a
    manifest that could vary one but not the other could not express
    "the same guest, different hardware" at all.
    """
    return variant.get("qemu_devices", manifest.get("qemu_devices", []))


def manifest_variants(manifest: dict) -> list[dict]:
    variants = manifest.get("variants")
    if variants is not None:
        return variants
    return [{
        "config": manifest.get("config"),
        "steps": manifest.get("steps", []),
    }]


def demo_presets() -> tuple[str, ...]:
    """Every composition a demo asks to be verified on.

    Read rather than listed: the presets a run may build are whatever
    the manifests name, and a second list of them is the copy that goes
    stale.
    """
    named = {
        variant["preset"]
        for _name, demo in iter_demos()
        for variant in manifest_variants(demo)
        if variant.get("preset")
    }
    return tuple(sorted(named))


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
    # Every place a device list may be written is checked by the same
    # rule, or a variant becomes the way to smuggle one past it.
    for source in (manifest, *manifest_variants(manifest)):
        devices = source.get("qemu_devices", [])
        if not isinstance(devices, list) or not all(
            isinstance(device, str) and device for device in devices
        ):
            raise SystemExit(
                f"[nova demo] {demo_name}: qemu_devices must be a list of non-empty strings"
            )
    for guest in manifest.get("guests", []):
        abi.validate_guest(
            f"[nova demo] {demo_name}: guest '{guest.get('name')}'", guest
        )
