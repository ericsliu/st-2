#!/usr/bin/env python3
"""Hook libc exit/abort family in Uma (via ZygiskFrida gadget) and print
a native backtrace when any is called. Identifies the real exit-triggering code.

Run BEFORE the shield unpacks so we catch the first call. Combine with the
shield_bypass patches if you want both.
"""
from __future__ import annotations
import argparse
import sys
import time
import frida

HOST = "127.0.0.1:27042"

AGENT = r"""
const targets = ['exit', '_exit', '_Exit', 'abort', 'raise', 'kill', 'tgkill', 'pthread_exit'];
const libcPaths = ['libc.so', 'libc.so.6'];

// Log modules at startup so we know what's visible
const mods = Process.enumerateModules();
send({type: 'modules', count: mods.length,
      sample: mods.slice(0, 30).map(function (m) { return m.name + '@' + m.base.toString(); })});

function findSym(name) {
    let p = null;
    try { p = Module.findExportByName(null, name); } catch (e) {}
    if (p) return p;
    for (const lib of libcPaths) {
        try { p = Module.findExportByName(lib, name); if (p) return p; } catch (e) {}
    }
    // Frida 17+: Process.getModuleByName returns a Module with own API
    try {
        const libc = Process.getModuleByName('libc.so');
        if (libc) {
            try {
                p = libc.findExportByName ? libc.findExportByName(name) : null;
                if (p) return p;
            } catch (e) {}
            try {
                const exps = libc.enumerateExports();
                for (let i = 0; i < exps.length; i++) {
                    if (exps[i].name === name) return exps[i].address;
                }
            } catch (e) {}
            try {
                const syms = libc.enumerateSymbols();
                for (let i = 0; i < syms.length; i++) {
                    if (syms[i].name === name) return syms[i].address;
                }
            } catch (e) {}
        }
    } catch (e) {
        send({type: 'proc_getmod_err', err: e.toString()});
    }
    return null;
}

const attached = [];

function bt(ctx) {
    try {
        const frames = Thread.backtrace(ctx, Backtracer.ACCURATE);
        const out = [];
        for (let i = 0; i < frames.length; i++) {
            try {
                const sym = DebugSymbol.fromAddress(frames[i]);
                out.push(sym.toString());
            } catch (e) {
                out.push(frames[i].toString());
            }
        }
        return out;
    } catch (e) {
        return ['<backtrace failed: ' + e + '>'];
    }
}

for (let i = 0; i < targets.length; i++) {
    const name = targets[i];
    let p = findSym(name);
    if (p === null) {
        send({type: 'sym_missing', fn: name});
        continue;
    }
    try {
        Interceptor.attach(p, {
            onEnter: function (args) {
                let arg0 = '';
                try { arg0 = args[0].toString(); } catch (e) { arg0 = '<err>'; }
                send({
                    type: 'exit_called',
                    fn: name,
                    arg0: arg0,
                    backtrace: bt(this.context),
                });
            }
        });
        attached.push(name + ' @ ' + p.toString());
    } catch (e) {
        send({type: 'attach_fail', fn: name, error: e.toString()});
    }
}

send({type: 'ready', hooks: attached});
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="Gadget")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--duration", type=float, default=90.0,
                        help="seconds to keep listening after attach")
    args = parser.parse_args()

    dev = frida.get_device_manager().add_remote_device(args.host)
    print(f"[*] connecting to {args.host}")

    session = dev.attach(args.target)
    print(f"[*] attached to {args.target}")

    script = session.create_script(AGENT)

    def on_msg(msg, _data):
        if msg.get("type") == "send":
            payload = msg["payload"]
            t = payload.get("type")
            if t == "ready":
                print(f"[ready] hooks installed: {payload['hooks']}")
            elif t == "modules":
                print(f"[modules] count={payload['count']} sample={payload['sample']}")
            elif t == "sym_missing":
                print(f"[sym_missing] {payload['fn']}")
            elif t == "exit_called":
                print(f"\n[EXIT] fn={payload['fn']} arg0={payload['arg0']}")
                for frame in payload["backtrace"]:
                    print(f"    {frame}")
            elif t == "attach_fail":
                print(f"[attach_fail] {payload['fn']}: {payload['error']}")
            else:
                print(f"[msg] {payload}")
        elif msg.get("type") == "error":
            print(f"[err] {msg.get('description')}")

    script.on("message", on_msg)
    script.load()

    print(f"[*] watching for {args.duration}s...")
    deadline = time.time() + args.duration
    while time.time() < deadline:
        time.sleep(0.5)

    try:
        session.detach()
    except Exception:
        pass


if __name__ == "__main__":
    main()
