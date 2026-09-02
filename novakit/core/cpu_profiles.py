"""Which instruction set a build may emit, and which one a run provides.

Two facts that were three declarations. The compiler's `-mcpu`, the CPU
QEMU is told to be, and the identity a guest reads out of its device
tree all describe the same choice, and each used to be written down
separately — so a build tuned for one core could be executed on another
with nothing to notice.

A build is runnable on a runtime when the runtime **provides** every
capability the build **requires**. An inclusion, not an equality: a
binary asking only for the baseline runs on a superset core, which is
exactly the case a name comparison gets wrong. Capabilities are codegen
contract tokens rather than CPU names for the same reason — CPU names
have no order, and a generation number is not one either.

The data is read from `cpu_profiles.json` beside the AArch64 HAL, which
CMake reads with `string(JSON)` and this module with `json`. Neither
side parses the other's syntax, and neither holds a second copy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache

from . import config

PROFILES = config.REPO / "src" / "hal" / "arch" / "aarch64" / "cpu_profiles.json"


class UnknownProfile(KeyError):
    """No profile of that name, so there is nothing to build or run."""


class Incompatible(Exception):
    """This build cannot run on this runtime, and says which token is missing."""


@dataclass(frozen=True)
class CpuBuildProfile:
    """What the compiler is allowed to emit."""

    name: str
    mcpu: str
    requires: frozenset[str]


@dataclass(frozen=True)
class CpuRuntimeProfile:
    """What the CPU underneath provides, and what a guest is told it is."""

    name: str
    qemu: str
    provides: frozenset[str]
    presented_identity: str


@cache
def _document() -> dict:
    return json.loads(PROFILES.read_text(encoding="utf-8"))


def _named(kind: str, name: str) -> dict:
    entries = _document()[kind]
    if name not in entries:
        raise UnknownProfile(
            f"no {kind} CPU profile '{name}'; {PROFILES.name} has "
            f"{', '.join(sorted(entries))}"
        )
    return entries[name]


def build_profile(name: str) -> CpuBuildProfile:
    entry = _named("build", name)
    return CpuBuildProfile(name, entry["mcpu"], frozenset(entry["requires"]))


def runtime_profile(name: str) -> CpuRuntimeProfile:
    entry = _named("runtime", name)
    return CpuRuntimeProfile(
        name, entry["qemu"], frozenset(entry["provides"]), entry["presented_identity"]
    )


def missing(build: CpuBuildProfile, runtime: CpuRuntimeProfile) -> frozenset[str]:
    """What the build needs and the runtime does not have."""
    return build.requires - runtime.provides


def require_compatible(build: CpuBuildProfile, runtime: CpuRuntimeProfile) -> None:
    """Refuse a pairing the runtime cannot carry, naming the shortfall.

    Named tokens rather than "incompatible": the reader has to know
    whether to change the build, the runtime, or the profile data.
    """
    absent = missing(build, runtime)
    if absent:
        raise Incompatible(
            f"a '{build.name}' build needs {', '.join(sorted(absent))}, which the "
            f"'{runtime.name}' runtime does not provide"
        )


def names(kind: str) -> tuple[str, ...]:
    """Every declared profile of one kind, for a CLI to offer or a test to loop."""
    return tuple(sorted(_document()[kind]))
