"""Pytest setup: make the fusion folder and the frozen CadQuery module importable."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FUSION_DIR = os.path.dirname(HERE)
RACK_DIR = os.path.dirname(FUSION_DIR)
for path in (FUSION_DIR, RACK_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)
