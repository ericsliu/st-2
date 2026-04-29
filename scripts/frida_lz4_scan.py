#!/usr/bin/env python3
"""WS-4 scan for LZ4 candidates in Uma Musume.

Attaches to the ZygiskFrida Gadget on 127.0.0.1:27042 and uses the agent's
scan_strings / scan_bytes / find_symbols rpc exports to hunt for LZ4
decompression routines in libnative.so / libil2cpp.so / libunity.so.

LZ4 is statically linked into Unity and IL2CPP; exports don't surface it,
so we look for:
  - ASCII "LZ4" / "lz4" / "LZ4HC" strings in rodata near the decompress fn
  - debug symbol names mentioning "lz4" or "decompress"
  - function prologues typical of LZ4_decompress_safe on arm64
"""
from __future__ import annotations
import sys, time
from pathlib import Path
import frida

HOST = "127.0.0.1:27042"
AGENT = Path(__file__).resolve().parents[1] / "frida_agent" / "dist" / "agent.js"

MODULES = ["libnative.so", "libil2cpp.so", "libunity.so", "libmain.so"]
NEEDLES = ["LZ4", "lz4", "LZ4HC", "LZ4F", "decompress", "Decompress"]


def on_message(message, data):
    if message["type"] == "error":
        print(f"[agent-error] {message.get('stack')}", file=sys.stderr)


def main() -> int:
    mgr = frida.get_device_manager()
    dev = mgr.add_remote_device(HOST)
    procs = dev.enumerate_processes()
    if not procs:
        print("[!] no processes", file=sys.stderr)
        return 2
    target = procs[0]
    print(f"[*] attach pid={target.pid}")
    session = dev.attach(target.pid)
    script = session.create_script(AGENT.read_text())
    script.on("message", on_message)
    script.load()
    time.sleep(0.4)
    rpc = script.exports_sync
    print(f"[*] ping: {rpc.ping()}")

    for mod in MODULES:
        print(f"\n### {mod}")
        # 1. Export-based (usually empty — static linkage)
        exports = rpc.find_lz4_candidates(mod)
        if exports:
            print(f"  [exports] {len(exports)} matches:")
            for e in exports[:10]:
                print(f"    - {e['name']} @ {e['address']}")

        # 2. Symbol-based (local debug syms may survive)
        syms = rpc.find_symbols(mod, "lz4|decompress")
        if syms:
            print(f"  [symbols] {len(syms)} matches:")
            for s in syms[:15]:
                print(f"    - {s['type']} {s['name']:<60} @ {s['address']}")

        # 3. String scan — the most reliable for statically linked LZ4
        strings = rpc.scan_strings(mod, NEEDLES)
        if strings:
            print(f"  [strings] {len(strings)} hits:")
            for s in strings[:15]:
                print(f"    - +{s['offset']:>12}  '{s['text']}'")
            if len(strings) > 15:
                print(f"    ... +{len(strings)-15} more")

    session.detach()
    mgr.remove_remote_device(HOST)
    return 0


if __name__ == "__main__":
    sys.exit(main())
