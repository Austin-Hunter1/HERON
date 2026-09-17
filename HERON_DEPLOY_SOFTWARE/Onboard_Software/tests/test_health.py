"""Health sampling must never raise and must report uptime."""

from heron_onboard.health import HealthMonitor


def test_sample_reports_uptime_and_never_raises():
    mon = HealthMonitor(["coretemp"], start_time=100.0)
    sample = mon.sample(now=160.0)
    assert sample.uptime_s == 60.0
    assert sample.cpu_pct >= 0.0
    assert isinstance(sample.temps_c, dict)
