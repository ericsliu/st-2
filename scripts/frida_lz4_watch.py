#!/usr/bin/env python3
"""Watch LZ4 decompress calls in Uma Musume in real-time.

Attaches to the Gadget, installs the LZ4 hook, and prints every call the
game makes to LZ4_decompress_safe_ext. Runs until Ctrl-C.

Use this at the title screen first to verify the hook fires, then after
login to see API response payloads.
"""
from __future__ import annotations
import argparse
import signal
import sys
import time
from pathlib import Path
import frida

HOST = "127.0.0.1:27042"
AGENT = Path(__file__).resolve().parents[1] / "frida_agent" / "dist" / "agent.js"

_stop = False


def handle_sigint(_signum, _frame):
    global _stop
    _stop = True


def on_message(message, data, verbose: bool):
    if message["type"] == "error":
        print(f"[agent-error] {message.get('stack')}", file=sys.stderr)
        return
    if message["type"] != "send":
        return
    p = message.get("payload") or {}
    t = p.get("type")
    if t == "lz4_call":
        print(
            f"[lz4 #{p['seq']:>4}] srcSize={p['srcSize']:>7} dstCap={p['dstCap']:>7} "
            f"ret={p['retval']:>7}  srcHead={p['srcHead'][:32]}  "
            f"plain={p['plaintextHead'][:32]}  arg4={p['arg4']} arg5={p['arg5']}",
            flush=True,
        )
    elif t == "lz4_hook":
        print(f"[hook] {p}", flush=True)
    elif verbose:
        print(f"[msg] {p}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--snapshot", type=int, default=64, help="bytes to capture from decoded buffer")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, handle_sigint)

    mgr = frida.get_device_manager()
    dev = mgr.add_remote_device(HOST)
    procs = dev.enumerate_processes()
    if not procs:
        print("[!] no processes", file=sys.stderr)
        return 2
    target = procs[0]
    print(f"[*] attach pid={target.pid}", flush=True)
    session = dev.attach(target.pid)
    script = session.create_script(AGENT.read_text())
    script.on("message", lambda m, d: on_message(m, d, args.verbose))
    script.load()
    time.sleep(0.3)
    print(f"[*] ping: {script.exports_sync.ping()}", flush=True)
    ok = script.exports_sync.install_lz4_hook(args.snapshot)
    if not ok:
        print("[!] hook install failed", file=sys.stderr)
        session.detach()
        return 3
    print("[*] LZ4 hook installed — watching. Ctrl-C to stop.", flush=True)

    while not _stop:
        time.sleep(0.2)

    print("\n[*] detaching")
    session.detach()
    mgr.remove_remote_device(HOST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
