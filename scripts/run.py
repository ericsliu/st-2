#!/usr/bin/env python3
"""Run a Python expression or script snippet using career_helper.

Usage:
    .venv/bin/python3 scripts/run.py "tap(540, 1540, delay=2)"
    .venv/bin/python3 scripts/run.py "img = screenshot('test'); print(detect_screen(img))"
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.career_helper import *  # noqa: F403

if __name__ == "__main__":
    code = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    if code:
        exec(code)
