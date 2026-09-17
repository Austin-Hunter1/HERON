"""The recorder's console output is saved next to the data (D-022)."""

import sys
import time
from pathlib import Path

from heron_onboard.capture.recorder_process import RecorderProcess

SCRIPT = (
    "import sys; print('[INFO] [B200] Operating over USB 3.'); "
    'print(\'{"event":"ready","ref_locked":true}\'); print(\'OOO\'); sys.stdout.flush()'
)


def test_recorder_log_file_has_every_line(tmp_path: Path):
    log_path = tmp_path / "b210_1" / "P_recorder_log.txt"
    proc = RecorderProcess("b210_1", [sys.executable, "-c", SCRIPT], log_path=log_path)
    proc.start()
    deadline = time.time() + 10
    while proc.poll() and time.time() < deadline:
        time.sleep(0.05)
    assert proc._reader is not None
    proc._reader.join(timeout=5)
    text = log_path.read_text()
    assert text.startswith("# ")  # The command line comes first.
    assert "[INFO] [B200] Operating over USB 3." in text
    assert '{"event":"ready","ref_locked":true}' in text
    assert "OOO" in text
    assert proc.ready and proc.status().ref_locked is True
