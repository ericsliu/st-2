#!/usr/bin/env python3
"""Attach Frida to Uma Musume by evicting its self-ptrace anti-debug child.

The game forks a small child process that immediately calls
ptrace(PTRACE_ATTACH, getppid()). Linux allows only one tracer per process,
so frida-server's attach fails with EPERM. If we kill that child, the
parent's TracerPid drops to 0 and a fresh Frida attach succeeds.

Unlike spawn mode (which trips the lib__4e06__ anti-tamper SDK's Frida
detection during boot), this attaches to an already-running game. If the
anti-tamper runs its Frida scan only once at startup, attach should land
cleanly. Untested hypothesis — run this and see.

Prereqs:
  - MuMu rooted (uid=0 in adb shell)
  - frida-server running on device (scripts/frida_start.sh)
  - User has launched the game manually and it's past the title screen
  - Host has frida 17.9.1 in .venv
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from typing import Optional

import frida

DEVICE = "127.0.0.1:5555"
TARGET = "com.cygames.umamusume"


def adb(*args: str) -> str:
    out = subprocess.run(
        ["adb", "-s", DEVICE, *args],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def find_game_pids() -> list[int]:
    """Return all pids matching the game's package name, sorted ascending."""
    out = adb("shell", f"pgrep -f {TARGET}")
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    pids.sort()
    return pids


def tracer_pid(pid: int) -> int:
    """Read TracerPid from /proc/<pid>/status. Returns 0 if untraced."""
    out = adb("shell", f"cat /proc/{pid}/status 2>/dev/null")
    for line in out.splitlines():
        if line.startswith("TracerPid:"):
            return int(line.split()[1])
    return -1  # process dead or unreadable


def kill_pid(pid: int) -> None:
    adb("shell", f"kill -9 {pid}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="frida_agent/dist/agent.js",
                        help="Path to compiled Frida agent bundle")
    parser.add_argument("--dry-run", action="store_true",
                        help="Find the ptrace child and report; do not kill or attach")
    parser.add_argument("--wait-after-kill", type=float, default=0.5,
                        help="Seconds to wait for /proc/status to refresh after kill")
    args = parser.parse_args()

    # 1. Find candidate game processes.
    pids = find_game_pids()
    if not pids:
        print(f"[!] no processes matching {TARGET!r}; launch the game first", file=sys.stderr)
        return 2
    print(f"[*] {TARGET} processes: {pids}")

    # The parent is typically the lowest pid (fork order). Walk pids and
    # find one that has a TracerPid set — that's the real main process.
    parent: Optional[int] = None
    ptracer: Optional[int] = None
    for pid in pids:
        tp = tracer_pid(pid)
        print(f"    pid={pid}  TracerPid={tp}")
        if tp > 0 and parent is None:
            parent = pid
            ptracer = tp

    if parent is None:
        # No self-ptrace detected. Either anti-debug didn't kick in, or
        # we can just attach to the lowest pid directly.
        print("[*] no self-ptrace observed; attaching to lowest pid directly")
        parent = pids[0]
    else:
        print(f"[*] game main pid={parent}, self-ptrace child pid={ptracer}")

        if args.dry_run:
            print("[dry-run] stopping here; nothing killed or attached")
            return 0

        # 2. Kill the ptracer child.
        print(f"[*] killing ptracer pid {ptracer}")
        kill_pid(ptracer)
        time.sleep(args.wait_after_kill)

        # 3. Verify TracerPid cleared.
        tp_after = tracer_pid(parent)
        print(f"[*] after kill: TracerPid={tp_after}")
        if tp_after > 0:
            print("[!] TracerPid still set; a replacement ptracer may have spawned", file=sys.stderr)
            return 3
        if tp_after < 0:
            print(f"[!] parent pid {parent} is gone — game may have self-exited on ptracer loss",
                  file=sys.stderr)
            return 4

    # 4. Frida attach.
    print(f"[*] frida attach to pid {parent}")
    device = frida.get_usb_device(timeout=5)
    session = device.attach(parent)

    # 5. Load agent.
    with open(args.agent) as f:
        code = f.read()
    script = session.create_script(code)

    def on_message(msg, data):
        if msg.get("type") == "send":
            print(f"[agent] {msg.get('payload')}")
        elif msg.get("type") == "error":
            print(f"[agent-err] {msg.get('stack', msg)}", file=sys.stderr)
    script.on("message", on_message)
    script.load()

    time.sleep(0.3)
    print(f"[*] ping: {script.exports_sync.ping()}")

    modules = script.exports_sync.report_modules("libnative|libmain|libil2cpp|lib__4e06__")
    print(f"[*] modules matching filter: {len(modules)}")
    for m in modules:
        print(f"    - {m['name']:<22} base={m['base']:>14} size={m['size']:>10}")

    lz4 = script.exports_sync.find_lz4_candidates()
    print(f"[*] LZ4 candidates in libnative.so: {len(lz4)}")
    for c in lz4:
        print(f"    - {c['name']:<40} @ {c['address']}")

    print("[*] attach successful; holding session. Ctrl-C to detach.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
