#!/usr/bin/env python3
"""Install exit traps FIRST, then install the LZ4 hook. When Uma's anti-cheat
kills the game, we'll capture a backtrace showing where the exit originates."""
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

    def on_message(msg, _data):
        if msg["type"] == "send":
            p = msg.get("payload") or {}
            t = p.get("type")
            if t == "exit_trap":
                print(f"\n### EXIT TRAP FIRED: {p['fn']} arg0={p['arg0']} tid={p['tid']}", flush=True)
                for i, frame in enumerate(p.get("backtrace", [])):
                    print(f"  #{i:<2} {frame}", flush=True)
            elif t in ("exit_trap_installed", "lz4_hook", "lz4_call", "antidebug"):
                print(f"[msg] {p}", flush=True)

    script.on("message", on_message)
    script.load()
    time.sleep(0.3)
    print(f"[*] ping: {script.exports_sync.ping()}", flush=True)
    # Install exit traps BEFORE LZ4 hook
    script.exports_sync.install_exit_traps()
    time.sleep(0.3)
    print("[*] exit traps in. Installing LZ4 hook...", flush=True)
    script.exports_sync.install_lz4_hook(64)
    t0 = time.time()
    while True:
        time.sleep(1)
        elapsed = int(time.time() - t0)
        try:
            script.exports_sync.ping()
            if elapsed % 5 == 0:
                print(f"[{elapsed:>3}s] alive", flush=True)
        except Exception as e:
            print(f"[{elapsed:>3}s] DEAD ({type(e).__name__})", flush=True)
            time.sleep(0.5)  # give any last messages a moment
            return 1


if __name__ == "__main__":
    sys.exit(main())
