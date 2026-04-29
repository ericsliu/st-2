#!/usr/bin/env python3
"""Attach to the Gadget but do nothing beyond ping — to test whether the
Frida session alone triggers Uma's ~60s self-exit, independent of any hook."""
from __future__ import annotations
import sys, time
from pathlib import Path
import frida

HOST = "127.0.0.1:27042"
AGENT = Path(__file__).resolve().parents[1] / "frida_agent" / "dist" / "agent.js"


def main() -> int:
    mgr = frida.get_device_manager()
    dev = mgr.add_remote_device(HOST)
    procs = dev.enumerate_processes()
    print(f"[*] attach pid={procs[0].pid}", flush=True)
    session = dev.attach(procs[0].pid)
    script = session.create_script(AGENT.read_text())
    script.load()
    time.sleep(0.3)
    print(f"[*] ping: {script.exports_sync.ping()}", flush=True)
    print("[*] attached idle, holding session open", flush=True)
    t0 = time.time()
    while True:
        time.sleep(5)
        elapsed = int(time.time() - t0)
        try:
            pong = script.exports_sync.ping()
            print(f"[{elapsed:>3}s] alive ping={pong}", flush=True)
        except Exception as e:
            print(f"[{elapsed:>3}s] DEAD {type(e).__name__}: {e}", flush=True)
            return 1


if __name__ == "__main__":
    sys.exit(main())
