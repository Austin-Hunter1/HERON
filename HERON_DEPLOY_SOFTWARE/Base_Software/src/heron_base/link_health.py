"""Link state from telemetry age (REQUIREMENTS B3). Pure logic."""

from __future__ import annotations

from enum import StrEnum


class LinkHealth(StrEnum):
    NO_DATA = "NO DATA"  # Nothing received since start.
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    LOST = "LOST"


def compute_link_health(age_s: float | None, degraded_s: float, lost_s: float) -> LinkHealth:
    """Map the age of the last telemetry to a link state."""
    if age_s is None:
        return LinkHealth.NO_DATA
    if age_s >= lost_s:
        return LinkHealth.LOST
    if age_s >= degraded_s:
        return LinkHealth.DEGRADED
    return LinkHealth.CONNECTED
