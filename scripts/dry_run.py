"""Dry-run turn evaluation — thin wrapper around do_one_turn (dry-run mode).

Captures screen, assembles state, scans training tiles, scores them,
and prints the decision. No taps that change game state.

Usage:
    .venv/bin/python scripts/dry_run.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Reuse do_one_turn's full pipeline in dry-run mode (no --execute)
from scripts.do_one_turn import main as do_one_turn_main

if __name__ == "__main__":
    # Strip argv so do_one_turn sees no --execute flag (dry-run by default)
    sys.argv = [sys.argv[0]]
    sys.exit(do_one_turn_main())
