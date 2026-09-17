"""heron_base: the ground station laptop software (D-005).

- ``config``: the base config model.
- ``link_health``: CONNECTED / DEGRADED / LOST from telemetry age (B3).
- ``link_client``: receive telemetry, send commands, match acks, retry.
- ``flight_log``: JSONL log of all telemetry and commands (B5).
- ``gnss``: read the GNSS receiver, log raw data, parse NMEA, split
  RTCM and pass it to a correction sink (B4).
- ``tui``: the terminal display, a thin layer over ``link_client`` (B6).
- ``cli``: the ``heron-base`` command.
"""
