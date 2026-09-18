"""One logging setup for all HERON programs.

Rules (REQUIREMENTS O13, B5):

- Every event goes to a timestamped log file that does not depend on
  the ground link.
- Timestamps are UTC.
- Verbosity comes from config.
- Logs also go to the console, so ``journalctl`` and a foreground run
  show the same lines.
"""

from __future__ import annotations

import logging
import logging.handlers
import time
from pathlib import Path

LOG_FORMAT = "%(asctime)s.%(msecs)03dZ %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


def setup_logging(
    level: str,
    log_dir: Path | None,
    program: str,
    max_bytes: int = 20_000_000,
    backup_count: int = 20,
) -> logging.Logger:
    """Configure the root logger and return the program logger.

    ``level`` is a standard name such as ``"INFO"``. When ``log_dir`` is
    not ``None``, add a rotating file handler at
    ``<log_dir>/<program>.log``. Create ``log_dir`` if it is missing.
    """
    numeric = logging.getLevelName(level.upper())
    if not isinstance(numeric, int):
        raise ValueError(f"Unknown log level: {level}")

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    formatter.converter = time.gmtime  # Force UTC in the log lines.

    root = logging.getLogger()
    root.setLevel(numeric)
    # Remove old handlers so repeated calls (tests) do not duplicate lines.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / f"{program}.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    return logging.getLogger(program)
