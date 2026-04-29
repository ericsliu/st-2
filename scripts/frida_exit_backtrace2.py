#!/usr/bin/env python3
"""Catch Uma's deliberate-crash exit. Hooks libc kill/tgkill/syscall/pthread_kill,
the shield's syscall() wrapper (inline svc at unpacked_base + 0x12130), and sets
a Frida exception handler for SIGSEGV. Logs backtraces.
"""
from __future__ import annotations
import argparse
import sys
import time
import frida

HOST = "127.0.0.1:27042"

AGENT = r"""
const SIG = "checkLoadPath_extractNativeLibs_true";
const SIG_OFFSET = 0x12205;
const SYSCALL_WRAPPER_OFFSET = 0x12130;  // the svc #0 we found
const targets = ['exit', '_exit', '_Exit', 'abort', 'raise', 'kill', 'tgkill',
                 'pthread_exit', 'pthread_kill', 'syscall'];

function memmem(base, size, needle) {
    const first = needle[0];
    const nlen = needle.length;
    let end = size - nlen;
    for (let i = 0; i < end; i++) {
        if (base.add(i).readU8() !== first) continue;
        let ok = true;
        for (let j = 1; j < nlen; j++) {
            if (base.add(i + j).readU8() !== needle[j]) { ok = false; break; }
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
        if (r.file) continue;
        if (r.size < 0x13000) continue;
        try {
            const idx = memmem(r.base, r.size, needle);
            if (idx < 0) continue;
            return r.base.add(idx).sub(SIG_OFFSET);
        } catch (e) {}
    }
    return null;
}

function findLibcSym(name) {
    let p = null;
    try { p = Module.findExportByName('libc.so', name); } catch (e) {}
    if (p) return p;
    try {
        const libc = Process.getModuleByName('libc.so');
        if (libc) {
            try { p = libc.findExportByName(name); if (p) return p; } catch (e) {}
            try {
                const syms = libc.enumerateSymbols();
                for (let i = 0; i < syms.length; i++)
                    if (syms[i].name === name) return syms[i].address;
            } catch (e) {}
        }
    } catch (e) {}
    return null;
}

function bt(ctx) {
    const out = [];
    try {
        const frames = Thread.backtrace(ctx, Backtracer.ACCURATE);
        for (let i = 0; i < frames.length && i < 25; i++) {
            try { out.push(DebugSymbol.fromAddress(frames[i]).toString()); }
            catch (e) { out.push(frames[i].toString()); }
        }
    } catch (e) { out.push('<bt err: ' + e + '>'); }
    return out;
}

const attached = [];

// libc hooks
for (let i = 0; i < targets.length; i++) {
    const name = targets[i];
    const p = findLibcSym(name);
    if (!p) { send({type: 'missing', fn: name}); continue; }
    try {
        Interceptor.attach(p, {
            onEnter: function (args) {
                send({type: 'exit_called', fn: name,
                      arg0: args[0].toString(), arg1: args[1].toString(),
                      arg2: args[2].toString(),
                      backtrace: bt(this.context)});
            }
        });
        attached.push(name + '@' + p);
    } catch (e) { send({type: 'hook_fail', fn: name, err: e.toString()}); }
}

// shield syscall wrapper hook
const shieldBase = findShieldBase();
if (shieldBase) {
    const wrapper = shieldBase.add(SYSCALL_WRAPPER_OFFSET);
    // wrapper runs INTO the svc — we want to intercept BEFORE that.
    // The wrapper is actually a leaf function whose entry is earlier.
    // Scan backward to find prologue (sub sp, ...).
    let entry = null;
    for (let off = 0; off <= 0x80; off += 4) {
        const candidate = wrapper.sub(off);
        try {
            const w = candidate.readU32();
            // Look for `sub sp, sp, #imm` or first function boundary marker.
            // aarch64 "sub sp, sp, #imm" = 0xD10003FF | (imm<<10) roughly.
            // Just take 64 bytes back as a rough window.
            if ((w & 0xFF0003FF) === 0xD10003FF) {
                entry = candidate; break;
            }
        } catch (e) { break; }
    }
    send({type: 'shield', base: shieldBase.toString(),
          wrapper: wrapper.toString(),
          entry: entry ? entry.toString() : 'not_found'});
    if (entry) {
        try {
            Interceptor.attach(entry, {
                onEnter: function (args) {
                    // x8 holds syscall num after `mov x8, x7` in wrapper,
                    // but by the time we hit entry, x7 holds what will become x8.
                    // Actually the wrapper pattern is called like syscall(nr, a0, a1, ...)
                    // where nr is args[0] initially. Then moved into x8 before svc.
                    send({type: 'shield_syscall',
                          nr: this.context.x0.toString(),
                          arg0: this.context.x1.toString(),
                          arg1: this.context.x2.toString(),
                          arg2: this.context.x3.toString(),
                          backtrace: bt(this.context)});
                }
            });
        } catch (e) { send({type: 'shield_hook_fail', err: e.toString()}); }
    }
} else {
    send({type: 'shield_missing'});
}

// Exception handler (catches SIGSEGV + friends before kernel kills us)
Process.setExceptionHandler(function (details) {
    send({type: 'exception', kind: details.type,
          addr: details.address.toString(),
          memory: details.memory ? {op: details.memory.operation,
                                    addr: details.memory.address.toString()} : null,
          context_pc: details.context.pc.toString(),
          context_lr: details.context.lr ? details.context.lr.toString() : null,
          backtrace: bt(details.context)});
    return false;  // don't swallow
});

send({type: 'ready', hooks: attached});
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="Gadget")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--duration", type=float, default=80.0)
    args = parser.parse_args()

    dev = frida.get_device_manager().add_remote_device(args.host)
    session = dev.attach(args.target)
    script = session.create_script(AGENT)

    def on_msg(msg, _data):
        if msg.get("type") == "send":
            payload = msg["payload"]
            t = payload.get("type")
            if t in ("ready", "shield", "missing", "hook_fail", "shield_missing",
                     "shield_hook_fail"):
                print(f"[{t}] {payload}")
            elif t == "exit_called":
                print(f"\n[EXIT_CALLED] fn={payload['fn']} "
                      f"arg0={payload['arg0']} arg1={payload['arg1']} "
                      f"arg2={payload['arg2']}")
                for f in payload['backtrace']:
                    print(f"    {f}")
            elif t == "shield_syscall":
                print(f"\n[SHIELD_SYSCALL] nr={payload['nr']} "
                      f"a0={payload['arg0']} a1={payload['arg1']} a2={payload['arg2']}")
                for f in payload['backtrace']:
                    print(f"    {f}")
            elif t == "exception":
                print(f"\n[EXCEPTION] {payload['kind']} @ {payload['addr']}")
                print(f"    memory: {payload['memory']}")
                print(f"    pc={payload['context_pc']}  lr={payload['context_lr']}")
                for f in payload['backtrace']:
                    print(f"    {f}")
            else:
                print(f"[msg] {payload}")
        elif msg.get("type") == "error":
            print(f"[err] {msg.get('description')}")

    script.on("message", on_msg)
    script.load()

    print(f"[*] listening for {args.duration}s...")
    t0 = time.time()
    while time.time() - t0 < args.duration:
        time.sleep(0.5)

    try:
        session.detach()
    except Exception:
        pass


if __name__ == "__main__":
    main()
