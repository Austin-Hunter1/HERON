"""Link state from telemetry age (B3)."""

from heron_base.link_health import LinkHealth, compute_link_health


def test_thresholds():
    assert compute_link_health(None, 3, 10) == LinkHealth.NO_DATA
    assert compute_link_health(0.0, 3, 10) == LinkHealth.CONNECTED
    assert compute_link_health(2.9, 3, 10) == LinkHealth.CONNECTED
    assert compute_link_health(3.0, 3, 10) == LinkHealth.DEGRADED
    assert compute_link_health(9.9, 3, 10) == LinkHealth.DEGRADED
    assert compute_link_health(10.0, 3, 10) == LinkHealth.LOST
