"""GNSS receiver handling on the ground station (B4).

- ``nmea``: parse GGA sentences for the fix display.
- ``rtcm``: split RTCM3 frames out of the raw byte stream.
- ``correction``: where RTCM3 frames go (none, serial).
- ``receiver``: the serial reader thread that logs raw bytes and feeds
  the parsers.
"""

from heron_base.gnss.receiver import GnssReceiver, GnssStatus

__all__ = ["GnssReceiver", "GnssStatus"]
