#!/usr/bin/env python3
"""Quick smoke test for ZygiskFrida on BlueStacks Air.

Attach to the Gadget via TCP on 127.0.0.1:27042, load the WS-2 agent,
and enumerate interesting modules including libnative.so.
"""
from __future__ import annotations
import sys, time
from pathlib import Path
import frida

HOST = "127.0.0.1:27042"
AGENT = Path(__file__).resolve().parents[1] / "frida_agent" / "dist" / "agent.js"


def on_message(message, data):
    if message["type"] == "send":
        payload = message.get("payload") or {}
        print(f"[agent] {payload}")
        if data:
            print(f"[agent] (+{len(data)} bytes)")
    elif message["type"] == "error":
        print(f"[agent-error] {message.get('stack')}", file=sys.stderr)


def main() -> int:
    mgr = frida.get_device_manager()
    dev = mgr.add_remote_device(HOST)
    print(f"[*] device: {dev.name} ({dev.type})")
    procs = dev.enumerate_processes()
    print(f"[*] processes: {[(p.pid, p.name) for p in procs]}")
    if not procs:
        print("[!] no processes visible", file=sys.stderr)
        return 2
    target = procs[0]
    print(f"[*] attaching pid={target.pid} ({target.name})")
    session = dev.attach(target.pid)
    code = AGENT.read_text()
    script = session.create_script(code)
    script.on("message", on_message)
    script.load()
    time.sleep(0.4)
    print(f"[*] ping: {script.exports_sync.ping()}")
    print("[*] modules matching libnative|libmain|libil2cpp|lib__4e06__:")
    mods = script.exports_sync.report_modules("libnative|libmain|libil2cpp|lib__4e06__|libunity")
    for m in mods:
        print(f"    - {m['name']:<22} base={m['base']:>14} size={m['size']:>10}")
    print(f"[*] lz4 candidates:")
    lz4 = script.exports_sync.find_lz4_candidates()
    for c in lz4:
        print(f"    - {c['name']:<40} @ {c['address']}")
    session.detach()
    mgr.remove_remote_device(HOST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
