#!/usr/bin/env python3
"""Dump anon r-x memory regions + lib__4e06__.so's mapped range from a running Uma process.

Purpose: the shield library lib__4e06__.so is a packer stub; its real code is
decrypted at runtime into anonymous executable memory (mmap + mprotect). We want
the unpacked payload to analyze statically in Ghidra.

No Interceptor, no hooks — memory reads only. Pre-req: Hachimi zygisk disabled,
Uma launched by user, ZygiskFrida gadget listening on 127.0.0.1:27042.

Output: /tmp/uma_dump/<base>_<size>.bin for each anon rx region + a manifest.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path
import frida

HOST = "127.0.0.1:27042"
OUT_DIR = Path("/tmp/uma_dump")

AGENT = r"""
rpc.exports = {
    snapshot: function () {
        const modules = [];
        Process.enumerateModules().forEach(function (m) {
            modules.push({
                name: m.name,
                base: m.base.toString(),
                size: m.size,
                path: m.path
            });
        });
        const ranges = [];
        Process.enumerateRanges({protection: 'r-x', coalesce: false}).forEach(function (r) {
            ranges.push({
                base: r.base.toString(),
                size: r.size,
                protection: r.protection,
                file: r.file ? {path: r.file.path, offset: r.file.offset} : null
            });
        });
        return {modules: modules, ranges: ranges, pid: Process.id};
    },

    readMem: function (base, size) {
        try {
            const buf = ptr(base).readByteArray(size);
            send({type: 'mem', base: base, size: size}, buf);
            return {ok: true};
        } catch (e) {
            send({type: 'mem_err', base: base, size: size, error: e.toString()});
            return {ok: false, error: e.toString()};
        }
    }
};
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-dump-bytes", type=int, default=64 * 1024 * 1024,
                    help="Cap total bytes dumped across all regions")
    ap.add_argument("--min-region-size", type=int, default=0x1000,
                    help="Skip ranges smaller than this")
    ap.add_argument("--max-region-size", type=int, default=8 * 1024 * 1024,
                    help="Skip ranges larger than this (libil2cpp etc)")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    mgr = frida.get_device_manager()
    dev = mgr.add_remote_device(HOST)
    procs = dev.enumerate_processes()
    if not procs:
        print("[!] no processes listed by gadget — is Uma running?", flush=True)
        return 1
    pid = procs[0].pid
    print(f"[*] attach pid={pid}", flush=True)
    session = dev.attach(pid)
    script = session.create_script(AGENT)

    mem_queue = []

    def on_message(msg, data):
        if msg.get("type") == "send":
            payload = msg.get("payload", {})
            if isinstance(payload, dict) and payload.get("type") == "mem":
                mem_queue.append((payload, data))
            elif isinstance(payload, dict) and payload.get("type") == "mem_err":
                print(f"    agent error: {payload}", flush=True)
        elif msg.get("type") == "error":
            print(f"[!] JS error: {msg.get('description')}", flush=True)

    script.on("message", on_message)
    script.load()

    snap = script.exports_sync.snapshot()
    print(f"[*] snapshot: {len(snap['modules'])} modules, {len(snap['ranges'])} r-x ranges", flush=True)

    # Find lib__4e06__.so's own mapped range
    shield = None
    for m in snap["modules"]:
        if "__4e06__" in m["name"]:
            shield = m
            break
    print(f"[*] lib__4e06__.so: {shield}", flush=True)

    # Build a map of file-backed ranges, identify anon rx ranges
    anon = []
    file_backed = []
    for r in snap["ranges"]:
        if r["file"] is None:
            anon.append(r)
        else:
            file_backed.append(r)
    print(f"[*] {len(anon)} anon rx ranges, {len(file_backed)} file-backed rx ranges", flush=True)

    # Also dump lib__4e06__.so's file-backed mapping (for cross-ref)
    to_dump = []
    for r in anon:
        if r["size"] < args.min_region_size or r["size"] > args.max_region_size:
            continue
        to_dump.append(("anon", r))
    if shield is not None:
        to_dump.append(("shield_map", {"base": shield["base"], "size": shield["size"],
                                        "protection": "r-x", "file": {"path": shield["path"], "offset": 0}}))

    total = 0
    manifest = {"pid": pid, "shield_module": shield, "dumps": []}
    for tag, r in to_dump:
        if total >= args.max_dump_bytes:
            print(f"[!] hit max-dump-bytes cap, stopping", flush=True)
            break
        base = r["base"]
        size = min(r["size"], args.max_dump_bytes - total)
        print(f"[*] read {tag} base={base} size={size} file={r.get('file')}", flush=True)
        before = len(mem_queue)
        try:
            resp = script.exports_sync.read_mem(base, size)
        except Exception as e:
            print(f"    FAIL: {e}", flush=True)
            manifest["dumps"].append({"tag": tag, "base": base, "size": size, "error": str(e)})
            continue
        deadline = time.time() + 10.0
        while len(mem_queue) <= before and time.time() < deadline:
            time.sleep(0.02)
        if len(mem_queue) <= before:
            print(f"    timeout waiting for mem msg; resp={resp}", flush=True)
            manifest["dumps"].append({"tag": tag, "base": base, "size": size, "error": "timeout"})
            continue
        payload, data = mem_queue[-1]
        if data is None:
            print(f"    no data attached to message; resp={resp}", flush=True)
            manifest["dumps"].append({"tag": tag, "base": base, "size": size, "error": "no data"})
            continue
        buf = bytes(data)
        fname = f"{tag}_{base}_{size:x}.bin"
        out = OUT_DIR / fname
        out.write_bytes(buf)
        total += len(buf)
        print(f"    wrote {out} ({len(buf)} bytes)", flush=True)
        entry = {"tag": tag, "base": base, "size": len(buf), "file": fname,
                 "source_file": r.get("file")}
        manifest["dumps"].append(entry)

    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"[*] done, total={total} bytes across {len(manifest['dumps'])} dumps", flush=True)
    print(f"[*] manifest at {OUT_DIR / 'manifest.json'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
