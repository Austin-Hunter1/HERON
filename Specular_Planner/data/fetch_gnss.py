#!/usr/bin/env python3
"""Pull planning files for GNSS that share GPS L5 (1176.45 MHz).

  .venv/bin/python data/fetch_gnss.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.catalog import SOURCES, refresh_source  # noqa: E402


def main() -> None:
    for src in SOURCES:
        print(src["name"])
        try:
            info = refresh_source(src["id"])
            print(f"  {info['message']}")
        except Exception as exc:
            print(f"  skip: {exc}")


if __name__ == "__main__":
    main()
