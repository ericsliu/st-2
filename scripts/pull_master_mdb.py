#!/usr/bin/env python3
"""Pull master.mdb from the Android emulator and verify it.

Requires root access on the emulator.  MuMuPlayer allows toggling root
in settings — enable it before running this script, then disable it
before launching the game (Uma Musume blocks rooted devices).

Usage:
    python scripts/pull_master_mdb.py
    python scripts/pull_master_mdb.py --device 127.0.0.1:5555
    python scripts/pull_master_mdb.py --output data/master.mdb
    python scripts/pull_master_mdb.py --info   # Just inspect an existing file
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

# Global version package name
PACKAGE = "com.cygames.umamusume"
MASTER_PATH = f"/data/data/{PACKAGE}/files/master/master.mdb"

DEFAULT_OUTPUT = "data/master.mdb"


def run_adb(args: list[str], device: str | None = None) -> subprocess.CompletedProcess:
    cmd = ["adb"]
    if device:
        cmd.extend(["-s", device])
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def pull_master_mdb(device: str | None, output: str) -> bool:
    """Pull master.mdb from the device."""
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Pulling master.mdb from {MASTER_PATH} ...")

    # Try with root
    result = run_adb(["root"], device)
    if "adbd is already running as root" in result.stdout or result.returncode == 0:
        print("  ADB running as root")
    else:
        # Try su-based copy to a readable location
        print("  Direct root not available, trying su + cp workaround...")
        tmp_path = "/sdcard/master_tmp.mdb"
        run_adb(["shell", "su", "-c", f"cp {MASTER_PATH} {tmp_path}"], device)
        run_adb(["shell", "su", "-c", f"chmod 644 {tmp_path}"], device)
        result = run_adb(["pull", tmp_path, output], device)
        run_adb(["shell", "rm", tmp_path], device)
        if result.returncode == 0 and output_path.exists():
            print(f"  Pulled via su workaround → {output}")
            return True
        print("  su workaround failed. Enable root in MuMuPlayer settings first.")
        return False

    # Direct pull with root
    result = run_adb(["pull", MASTER_PATH, output], device)
    if result.returncode != 0:
        print(f"  Pull failed: {result.stderr.strip()}")
        return False

    if not output_path.exists():
        print("  File not found after pull")
        return False

    print(f"  Pulled → {output}")
    return True


def verify_master_mdb(path: str) -> bool:
    """Verify the file is valid SQLite and has expected tables."""
    db_path = Path(path)
    if not db_path.exists():
        print(f"File not found: {path}")
        return False

    size_mb = db_path.stat().st_size / (1024 * 1024)
    print(f"\nFile: {path} ({size_mb:.1f} MB)")

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as e:
        print(f"  Not a valid SQLite file: {e}")
        return False

    # Count tables
    cur = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
    table_count = cur.fetchone()[0]
    print(f"  Tables: {table_count}")

    # Check for key tables
    expected = [
        "text_data",
        "skill_data",
        "support_card_data",
        "chara_data",
        "single_mode_program",
        "single_mode_story_data",
    ]
    missing = []
    for table in expected:
        cur = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        if cur.fetchone()[0] == 0:
            missing.append(table)

    if missing:
        print(f"  Missing expected tables: {', '.join(missing)}")
    else:
        print(f"  All {len(expected)} key tables present ✓")

    # Sample counts
    for table in ["text_data", "skill_data", "support_card_data", "chara_data"]:
        try:
            cur = conn.execute(f"SELECT COUNT(*) FROM \"{table}\"")
            count = cur.fetchone()[0]
            print(f"  {table}: {count:,} rows")
        except sqlite3.Error:
            pass

    # Check for English text
    cur = conn.execute(
        "SELECT text FROM text_data WHERE category = 47 LIMIT 3"  # Skill names
    )
    samples = [r[0] for r in cur.fetchall()]
    if samples:
        print(f"  Sample skill names: {samples}")
        # Quick check if text looks like English
        ascii_ratio = sum(c.isascii() for s in samples for c in s) / max(
            sum(len(s) for s in samples), 1
        )
        if ascii_ratio > 0.8:
            print("  Text appears to be English ✓")
        else:
            print("  ⚠ Text may not be English (could be JP master.mdb)")

    conn.close()
    return len(missing) == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull and verify master.mdb")
    parser.add_argument("--device", "-d", default=None, help="ADB device serial")
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT, help="Output path")
    parser.add_argument(
        "--info", action="store_true",
        help="Just inspect an existing master.mdb (skip pull)",
    )
    args = parser.parse_args()

    if args.info:
        verify_master_mdb(args.output)
        return

    if pull_master_mdb(args.device, args.output):
        verify_master_mdb(args.output)
        print("\nDone. Remember to disable root in MuMuPlayer before launching the game.")
    else:
        print("\nFailed to pull master.mdb.")
        print("Make sure:")
        print("  1. MuMuPlayer is running with root enabled in settings")
        print("  2. The game has been installed and launched at least once")
        print(f"  3. ADB is connected: adb connect {args.device or '127.0.0.1:5555'}")
        sys.exit(1)


if __name__ == "__main__":
    main()
