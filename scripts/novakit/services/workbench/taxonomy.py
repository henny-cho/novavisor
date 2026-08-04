"""Event vocabulary, owned in exactly one place.

The bridge classifies console output with these names and the UI renders
them verbatim from the `topo` snapshot, so neither side ever invents a
label. A contract test holds every firmware log tag to a badge, which
turns log-vocabulary drift into a test failure instead of a blank panel.
"""

from __future__ import annotations

from enum import StrEnum


class Badge(StrEnum):
    """Subsystem label attached to every classified console event."""

    TRAP = "TRAP"
    IRQ = "IRQ"
    VGIC = "VGIC"
    GIC = "GIC"
    SCHED = "SCHED"
    SMP = "SMP"
    PSCI = "PSCI"
    DMA = "DMA"
    SMMU = "SMMU"
    WDG = "WDG"
    BOOT = "BOOT"
    MUX = "MUX"
    VUART = "VUART"
    FAULT = "FAULT"


class Severity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    CRIT = "CRIT"


def vocabulary() -> dict[str, list[str]]:
    """The vocabulary shipped to the UI inside the `topo` snapshot."""
    return {
        "badges": [badge.value for badge in Badge],
        "severities": [severity.value for severity in Severity],
    }
