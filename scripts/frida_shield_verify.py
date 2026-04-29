#!/usr/bin/env python3
"""Read shield writer/reader bytes to see whether bypass patches persist
in the current Uma process. Uses Memory.scanSync (native pattern matching)
so it's fast even across many anon ranges."""
from __future__ import annotations
import frida

HOST = "127.0.0.1:27042"

# "checkLoadPath_extractNativeLibs_true" as hex pattern (space-separated bytes).
SIG_BYTES = " ".join(f"{b:02x}" for b in b"checkLoadPath_extractNativeLibs_true")

AGENT = r"""
const SIG_PATTERN = %r;
const SIG_OFFSET = 0x12205;

function findShieldBase() {
    const ranges = Process.enumerateRanges({protection: 'r-x', coalesce: false});
    for (const r of ranges) {
        if (r.file) continue;
        if (r.size < 0x13000) continue;
        let matches;
        try { matches = Memory.scanSync(r.base, r.size, SIG_PATTERN); }
        catch (e) { continue; }
        if (matches && matches.length > 0) {
            const hit = matches[0].address;
            return hit.sub(SIG_OFFSET);
        }
    }
    return null;
}

rpc.exports = {
    scan: function () {
        const base = findShieldBase();
        if (!base) return {ok: false, err: 'shield not found'};
        const writer = base.add(0x11910);
        const reader = base.add(0x118ec);
        const wrapperWriter = base.add(0x16c64);
        const clearer = base.add(0x16c90);
        const syscallWrapper = base.add(0x12118);
        return {
            ok: true,
            base: base.toString(),
            writer_bytes: Array.from(new Uint8Array(writer.readByteArray(4))),
            reader_bytes: Array.from(new Uint8Array(reader.readByteArray(8))),
            wrapper_writer_bytes: Array.from(new Uint8Array(wrapperWriter.readByteArray(8))),
            clearer_bytes: Array.from(new Uint8Array(clearer.readByteArray(8))),
            syscall_wrapper_bytes: Array.from(new Uint8Array(syscallWrapper.readByteArray(16))),
        };
    }
};
""" % SIG_BYTES


def fmt_bytes(arr):
    return " ".join(f"{b:02x}" for b in arr)


def main():
    dev = frida.get_device_manager().add_remote_device(HOST)
    session = dev.attach("Gadget")
    script = session.create_script(AGENT)
    script.load()
    res = script.exports_sync.scan()
    if not res.get("ok"):
        print(f"[!] {res}")
        return
    print(f"shield_base:      {res['base']}")
    print(f"writer bytes:     {fmt_bytes(res['writer_bytes'])}  (ret = c0 03 5f d6)")
    print(f"reader bytes:     {fmt_bytes(res['reader_bytes'])}  (patched = 00 00 80 52 c0 03 5f d6)")
    print(f"wrapper_writer:   {fmt_bytes(res['wrapper_writer_bytes'])}")
    print(f"clearer:          {fmt_bytes(res['clearer_bytes'])}")
    print(f"syscall_wrapper:  {fmt_bytes(res['syscall_wrapper_bytes'])}")
    session.detach()


if __name__ == "__main__":
    main()
