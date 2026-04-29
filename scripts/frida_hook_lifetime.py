#!/usr/bin/env python3
"""Measure Uma survival time with the LZ4 hook installed.

Attach, install LZ4 hook immediately, then ping every 2s until the Gadget
dies. Prints time-to-death.
"""
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
            if p.get("type") == "lz4_call":
                print(
                    f"[LZ4 call #{p['seq']}] srcSize={p['srcSize']} dstCap={p['dstCap']} "
                    f"ret={p['retval']} srcHead={p['srcHead'][:24]} plain={p['plaintextHead'][:24]}",
                    flush=True,
                )
            elif p.get("type") == "lz4_hook":
                print(f"[hook] {p}", flush=True)

    script.on("message", on_message)
    script.load()
    time.sleep(0.3)
    print(f"[*] ping: {script.exports_sync.ping()}", flush=True)
    ok = script.exports_sync.install_lz4_hook(64)
    print(f"[*] install_lz4_hook -> {ok}", flush=True)
    t0 = time.time()
    while True:
        time.sleep(2)
        elapsed = int(time.time() - t0)
        try:
            script.exports_sync.ping()
            print(f"[{elapsed:>3}s] alive", flush=True)
        except Exception as e:
            print(f"[{elapsed:>3}s] DEAD ({type(e).__name__})", flush=True)
            return 1


if __name__ == "__main__":
    sys.exit(main())
