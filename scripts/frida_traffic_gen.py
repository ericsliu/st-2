#!/usr/bin/env python3
"""Loop run_one.py as a subprocess to generate API traffic for Frida probes.

Each iteration invokes .venv/bin/python scripts/run_one.py from the project
root. Exit codes and elapsed time are logged per run. SIGTERM/SIGINT cleanly
terminate any running child and exit 0.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python"
TARGET = ROOT / "scripts" / "run_one.py"

_child: subprocess.Popen | None = None
_stop = False


def _handle_signal(signum, frame):  # noqa: ARG001
    global _stop
    _stop = True
    if _child is not None and _child.poll() is None:
        try:
            _child.terminate()
        except Exception:
            pass


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    global _child
    i = 0
    while not _stop:
        i += 1
        t0 = time.monotonic()
        try:
            _child = subprocess.Popen([str(PY), str(TARGET)], cwd=str(ROOT))
            rc = _child.wait()
        except Exception as e:
            print(f"[traffic {i}] spawn err: {e}", flush=True)
            rc = -1
        dt = time.monotonic() - t0
        print(f"[traffic {i}] rc={rc} t={dt:.1f}s", flush=True)
        if _stop:
            break
        time.sleep(1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
