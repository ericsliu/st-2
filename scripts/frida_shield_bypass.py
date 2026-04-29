#!/usr/bin/env python3
"""Neutralize HyperTech CrackProof's detection writer in Uma Musume Global.

The shield (unpacked payload of lib__4e06__.so) runs integrity checks in a
timer thread. On a positive detection it writes the result into a global
detection buffer via FUN_6e54f56910 (offset +0x11910 in the 0x17000-byte
unpacked region). Game code elsewhere polls the flag via FUN_6e54f568ec
(+0x118ec) and calls exit(0) when the flag is nonzero.

We neutralize the writer by overwriting its first instruction with `ret`.
That's safe: at function entry x30 still holds the caller return address
(nothing has been saved yet), and the function returns void.

Run AFTER the shield has unpacked (Uma must be fully launched, ~5s in).
"""
from __future__ import annotations
import argparse
import sys
import time
import frida

HOST = "127.0.0.1:27042"

AGENT = r"""
// Sig that uniquely identifies the unpacked HyperTech CrackProof payload.
// String "checkLoadPath_extractNativeLibs_true" lives at payload_base + 0x12205.
const SIG = "checkLoadPath_extractNativeLibs_true";
const SIG_OFFSET = 0x12205;
const WRITER_OFFSET = 0x11910;
const READER_OFFSET = 0x118ec;

function memmem(haystack_ptr, haystack_size, needle_bytes) {
    // Simple byte-scan. needle_bytes is a JS array of ints.
    const first = needle_bytes[0];
    const nlen = needle_bytes.length;
    let end = haystack_size - nlen;
    for (let i = 0; i < end; i++) {
        if (haystack_ptr.add(i).readU8() !== first) continue;
        let ok = true;
        for (let j = 1; j < nlen; j++) {
            if (haystack_ptr.add(i + j).readU8() !== needle_bytes[j]) { ok = false; break; }
        }
        if (ok) return i;
    }
    return -1;
}

function findShieldBase() {
    const needle = [];
    for (let i = 0; i < SIG.length; i++) needle.push(SIG.charCodeAt(i));
    const ranges = Process.enumerateRanges({protection: 'r-x', coalesce: false});
    for (const r of ranges) {
        if (r.file) continue;               // only anon regions
        if (r.size < 0x13000) continue;     // must be big enough to contain sig
        try {
            const idx = memmem(r.base, r.size, needle);
            if (idx < 0) continue;
            const inferredBase = r.base.add(idx).sub(SIG_OFFSET);
            // Sanity: inferred base must be within the region.
            if (inferredBase.compare(r.base) < 0) continue;
            if (inferredBase.add(0x17000).compare(r.base.add(r.size)) > 0) continue;
            send({type: 'found', region_base: r.base.toString(), region_size: r.size,
                  sig_offset_in_region: idx, shield_base: inferredBase.toString()});
            return inferredBase;
        } catch (e) {
            // skip unreadable ranges
        }
    }
    return null;
}

rpc.exports = {
    neutralize: function () {
        const base = findShieldBase();
        if (base === null) {
            return {ok: false, error: 'shield payload not found in anon rx ranges'};
        }

        const writer = base.add(WRITER_OFFSET);
        const reader = base.add(READER_OFFSET);
        const origWriter = writer.readByteArray(4);
        const origReader = reader.readByteArray(8);

        // Patch writer: replace first instr with `ret` (0xd65f03c0 LE).
        Memory.patchCode(writer, 4, function (code) {
            code.writeByteArray([0xc0, 0x03, 0x5f, 0xd6]);
        });

        // Patch flag-reader: `mov w0, #0; ret` so callers always see 'clean'.
        Memory.patchCode(reader, 8, function (code) {
            code.writeByteArray([
                0x00, 0x00, 0x80, 0x52,   // mov w0, #0
                0xc0, 0x03, 0x5f, 0xd6,   // ret
            ]);
        });

        return {
            ok: true,
            shield_base: base.toString(),
            writer_addr: writer.toString(),
            reader_addr: reader.toString(),
            orig_writer_bytes: Array.from(new Uint8Array(origWriter)),
            orig_reader_bytes: Array.from(new Uint8Array(origReader)),
        };
    },

    verify: function () {
        const base = findShieldBase();
        if (base === null) return {ok: false, error: 'shield not found'};
        const writer = base.add(WRITER_OFFSET);
        const reader = base.add(READER_OFFSET);
        return {
            ok: true,
            writer_bytes: Array.from(new Uint8Array(writer.readByteArray(4))),
            reader_bytes: Array.from(new Uint8Array(reader.readByteArray(8))),
        };
    }
};
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="Gadget",
                        help="attach target name (frida-gadget default is 'Gadget')")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--verify-only", action="store_true",
                        help="just read the patch sites, don't write")
    parser.add_argument("--wait", type=float, default=0.0,
                        help="seconds to sleep after attach before patching")
    args = parser.parse_args()

    dev = frida.get_device_manager().add_remote_device(args.host)
    print(f"[*] attached to device {args.host}")

    try:
        session = dev.attach(args.target)
    except frida.ProcessNotFoundError:
        print(f"[!] process not found: {args.target}")
        sys.exit(1)
    print(f"[*] session attached to {args.target}")

    script = session.create_script(AGENT)
    messages: list = []

    def on_msg(msg, _data):
        messages.append(msg)
        if msg.get("type") == "send":
            print(f"[msg] {msg['payload']}")
        elif msg.get("type") == "error":
            print(f"[err] {msg.get('description')}")

    script.on("message", on_msg)
    script.load()

    if args.wait > 0:
        print(f"[*] waiting {args.wait}s for shield to unpack")
        time.sleep(args.wait)

    if args.verify_only:
        res = script.exports_sync.verify()
    else:
        res = script.exports_sync.neutralize()

    print(f"[result] {res}")
    session.detach()


if __name__ == "__main__":
    main()
