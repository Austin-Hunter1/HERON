"""Capture layer: run the recorder processes and write flight metadata.

- ``backend``: the ``CaptureBackend`` interface, the real
  ``RecorderBackend`` (one ``heron_recorder`` process per SDR), and
  ``FakeCaptureBackend`` for tests and demos.
- ``recorder_process``: wrap one recorder process; build its command
  line; parse its JSON status lines.
- ``metadata``: the per-flight ``metadata.json`` (O5).
- ``manager``: ``CaptureManager`` joins the backend and the metadata.
"""

from heron_onboard.capture.backend import CaptureBackend, FakeCaptureBackend, RecorderBackend
from heron_onboard.capture.manager import CaptureManager

__all__ = ["CaptureBackend", "CaptureManager", "FakeCaptureBackend", "RecorderBackend"]
