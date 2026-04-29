#!/usr/bin/env python3
"""Attach to Uma via ZygiskFrida gadget, install ONLY exit traps (no LZ4 hook).

Goal: catch the clean exit(0) that kills Uma ~2.7s after Hachimi's 'Hooking
finished' log line, with a stack backtrace showing the caller.

Usage: launch Uma first, wait for 'Listening on 127.0.0.1 TCP port 27042'
in logcat, then run this immediately.
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
    procs = None
    t_start = time.time()
    while time.time() - t_start < 10:
        try:
            procs = dev.enumerate_processes()
            if procs:
                break
        except Exception as e:
            last = e
        time.sleep(0.1)
    if not procs:
        print("[!] could not reach gadget", flush=True)
        return 1
    print(f"[*] attach pid={procs[0].pid} name={procs[0].name}", flush=True)
    session = dev.attach(procs[0].pid)
    script = session.create_script(AGENT.read_text())

    def on_message(msg, _data):
        if msg["type"] == "send":
            p = msg.get("payload") or {}
            t = p.get("type")
            if t == "exit_trap":
                print(f"\n### EXIT TRAP FIRED: {p['fn']} arg0={p.get('arg0')} tid={p['tid']}", flush=True)
                for i, frame in enumerate(p.get("backtrace", [])):
                    print(f"  #{i:<2} {frame}", flush=True)
            elif t in ("exit_trap_installed", "ready", "antidebug", "module_loaded"):
                print(f"[msg] {p}", flush=True)
        elif msg["type"] == "error":
            print(f"[ERROR] {msg}", flush=True)

    script.on("message", on_message)
    script.load()
    time.sleep(0.3)
    try:
        print(f"[*] ping: {script.exports_sync.ping()}", flush=True)
    except Exception as e:
        print(f"[!] ping failed: {e}", flush=True)
        return 1
    script.exports_sync.install_exit_traps()
    print("[*] exit traps installed; watching for crash...", flush=True)
    t0 = time.time()
    while True:
        time.sleep(0.5)
        elapsed = time.time() - t0
        try:
            script.exports_sync.ping()
            if int(elapsed * 2) % 4 == 0:
                print(f"[{elapsed:5.1f}s] alive", flush=True)
        except Exception as e:
            print(f"[{elapsed:5.1f}s] DEAD ({type(e).__name__}: {e})", flush=True)
            time.sleep(0.8)
            return 0


if __name__ == "__main__":
    sys.exit(main())
