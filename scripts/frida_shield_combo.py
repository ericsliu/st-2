#!/usr/bin/env python3
"""Combined shield bypass + kill-path tracer for Uma Global.

What it does when loaded into the frida-gadget:
  1. Locates unpacked shield via signature "checkLoadPath_extractNativeLibs_true"
     (payload base + 0x12205) using Memory.scanSync — fast native scan.
  2. Patches writer FUN+0x11910 -> ret.
  3. Patches reader FUN+0x118ec -> mov w0,#0; ret.
  4. Hooks shield's internal syscall wrapper at +0x12118 (leaf func ending at +0x12148,
     `svc #0` at +0x12130 after `mov x8, x7`). On ARM64 Linux, x8 holds the syscall
     number at svc time; in this wrapper the caller passes nr in x7.
  5. Hooks libc exit/_exit/_Exit/abort/raise/kill/tgkill/pthread_exit/pthread_kill/syscall.
  6. Installs Process.setExceptionHandler — logs SIGSEGV/BUS/etc and can optionally
     swallow with --swallow-fault (returns true → Frida masks the signal).
  7. Streams events continuously instead of exiting after a fixed window.

Run this AFTER Uma has fully launched (~5s past frida-gadget inject).
"""
from __future__ import annotations
import argparse
import sys
import time
import frida

HOST = "127.0.0.1:27042"

SIG_BYTES = " ".join(f"{b:02x}" for b in b"checkLoadPath_extractNativeLibs_true")

AGENT_TEMPLATE = r"""
const SIG_PATTERN = %r;
const SIG_OFFSET = 0x12205;
const WRITER_OFFSET = 0x11910;
const READER_OFFSET = 0x118ec;
const SYSCALL_WRAPPER_OFFSET = 0x12118;  // confirmed entry of leaf svc wrapper
const SWALLOW_FAULT = %s;
const INSTALL_HOOKS_PLACEHOLDER = true;

function findShieldBase() {
    const ranges = Process.enumerateRanges({protection: 'r-x', coalesce: false});
    for (const r of ranges) {
        if (r.file) continue;
        if (r.size < 0x13000) continue;
        let matches;
        try { matches = Memory.scanSync(r.base, r.size, SIG_PATTERN); }
        catch (e) { continue; }
        if (matches && matches.length > 0) return matches[0].address.sub(SIG_OFFSET);
    }
    return null;
}

function bt(ctx) {
    const out = [];
    try {
        const frames = Thread.backtrace(ctx, Backtracer.ACCURATE);
        for (let i = 0; i < frames.length && i < 30; i++) {
            try { out.push(DebugSymbol.fromAddress(frames[i]).toString()); }
            catch (e) { out.push(frames[i].toString()); }
        }
    } catch (e) { out.push('<bt err: ' + e + '>'); }
    return out;
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

// ---- step 1: locate + patch shield -------------------------------------
const base = findShieldBase();
if (!base) {
    send({type: 'fatal', err: 'shield not located'});
    throw new Error('shield not located');
}
send({type: 'shield_found', base: base.toString()});

const writer = base.add(WRITER_OFFSET);
const reader = base.add(READER_OFFSET);
const syscallEntry = base.add(SYSCALL_WRAPPER_OFFSET);

const origWriter = Array.from(new Uint8Array(writer.readByteArray(4)));
const origReader = Array.from(new Uint8Array(reader.readByteArray(8)));
const alreadyPatchedWriter = (origWriter[0] === 0xc0 && origWriter[1] === 0x03
                              && origWriter[2] === 0x5f && origWriter[3] === 0xd6);

try {
    Memory.patchCode(writer, 4, function (code) {
        code.writeByteArray([0xc0, 0x03, 0x5f, 0xd6]);  // ret
    });
    Memory.patchCode(reader, 8, function (code) {
        code.writeByteArray([
            0x00, 0x00, 0x80, 0x52,  // mov w0, #0
            0xc0, 0x03, 0x5f, 0xd6,  // ret
        ]);
    });
    send({type: 'patched',
          writer: writer.toString(), reader: reader.toString(),
          orig_writer: origWriter, orig_reader: origReader,
          already_patched_writer: alreadyPatchedWriter});
} catch (e) {
    send({type: 'patch_fail', err: e.toString()});
}

// ---- step 2: hook libc kill primitives ---------------------------------
const attached = [];
const libcTargets = ['exit', '_exit', '_Exit', 'abort', 'raise', 'kill', 'tgkill',
                     'pthread_exit', 'pthread_kill', 'syscall'];
if (!INSTALL_HOOKS) {
    send({type: 'hooks_skipped', reason: 'patches-only mode'});
} else {
for (let i = 0; i < libcTargets.length; i++) {
    const name = libcTargets[i];
    const p = findLibcSym(name);
    if (!p) { send({type: 'missing', fn: name}); continue; }
    try {
        Interceptor.attach(p, {
            onEnter: function (args) {
                send({type: 'libc_exit', fn: name,
                      arg0: args[0].toString(), arg1: args[1].toString(),
                      arg2: args[2].toString(), backtrace: bt(this.context)});
            }
        });
        attached.push(name + '@' + p);
    } catch (e) { send({type: 'hook_fail', fn: name, err: e.toString()}); }
}

// ---- step 3: hook shield svc wrapper -----------------------------------
try {
    Interceptor.attach(syscallEntry, {
        onEnter: function (args) {
            // Caller convention: nr in x7, args in x0..x6.
            send({type: 'shield_svc',
                  nr: this.context.x7.toString(),
                  x0: this.context.x0.toString(),
                  x1: this.context.x1.toString(),
                  x2: this.context.x2.toString(),
                  x3: this.context.x3.toString(),
                  x4: this.context.x4.toString(),
                  x5: this.context.x5.toString(),
                  x6: this.context.x6.toString(),
                  backtrace: bt(this.context)});
        }
    });
    attached.push('shield_svc@' + syscallEntry);
} catch (e) { send({type: 'shield_svc_hook_fail', err: e.toString()}); }
}  // end if (INSTALL_HOOKS)

// ---- step 4: exception handler -----------------------------------------
Process.setExceptionHandler(function (details) {
    send({type: 'exception',
          kind: details.type,
          addr: details.address.toString(),
          memory: details.memory ? {op: details.memory.operation,
                                    addr: details.memory.address.toString()} : null,
          context_pc: details.context.pc.toString(),
          context_lr: details.context.lr ? details.context.lr.toString() : null,
          backtrace: bt(details.context)});
    return SWALLOW_FAULT;
});

send({type: 'ready', hooks: attached, swallow_fault: SWALLOW_FAULT});
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="Gadget")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--duration", type=float, default=180.0,
                        help="seconds to keep the session alive")
    parser.add_argument("--swallow-fault", action="store_true",
                        help="return true from exception handler (mask the signal)")
    parser.add_argument("--patches-only", action="store_true",
                        help="skip all Interceptor.attach hooks — only apply byte patches")
    args = parser.parse_args()

    swallow = "true" if args.swallow_fault else "false"
    install_hooks = "false" if args.patches_only else "true"
    agent = (AGENT_TEMPLATE % (SIG_BYTES, swallow)).replace(
        "const INSTALL_HOOKS_PLACEHOLDER = true;",
        f"const INSTALL_HOOKS = {install_hooks};")

    dev = frida.get_device_manager().add_remote_device(args.host)
    print(f"[*] connecting to {args.host}")
    session = dev.attach(args.target)
    print(f"[*] attached to {args.target}")

    script = session.create_script(agent)

    def on_msg(msg, _data):
        if msg.get("type") == "send":
            p = msg["payload"]
            t = p.get("type")
            if t in ("ready", "shield_found", "patched", "patch_fail", "fatal",
                     "missing", "hook_fail", "shield_svc_hook_fail"):
                print(f"[{t}] {p}")
            elif t == "libc_exit":
                print(f"\n[LIBC_EXIT] fn={p['fn']} a0={p['arg0']} a1={p['arg1']} a2={p['arg2']}")
                for f in p["backtrace"]: print(f"    {f}")
            elif t == "shield_svc":
                print(f"\n[SHIELD_SVC] nr={p['nr']} "
                      f"x0={p['x0']} x1={p['x1']} x2={p['x2']} x3={p['x3']}")
                for f in p["backtrace"]: print(f"    {f}")
            elif t == "exception":
                print(f"\n[EXCEPTION] {p['kind']} @ {p['addr']}  "
                      f"pc={p['context_pc']}  lr={p['context_lr']}")
                print(f"    memory: {p['memory']}")
                for f in p["backtrace"]: print(f"    {f}")
            else:
                print(f"[msg] {p}")
        elif msg.get("type") == "error":
            print(f"[err] {msg.get('description')}")
            if msg.get("stack"):
                print(msg["stack"])

    script.on("message", on_msg)
    script.load()

    print(f"[*] listening for {args.duration}s (swallow_fault={args.swallow_fault})...")
    t0 = time.time()
    try:
        while time.time() - t0 < args.duration:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    try: session.detach()
    except Exception: pass


if __name__ == "__main__":
    main()
