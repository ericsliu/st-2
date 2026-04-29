#!/usr/bin/env python3
"""Hook Java-side exit paths via JVM: System.exit(), Process.killProcess(),
Activity.finishAndRemoveTask(), Runtime.exit(). Log backtraces.

Also installs libc exit traps (as extra coverage).

Run immediately after Uma launch. Background the script so Python is ready
while the Frida gadget is coming up.
"""
from __future__ import annotations
import sys, time
from pathlib import Path
import frida

HOST = "127.0.0.1:27042"

JS_HOOK = r"""
function btStrings(ctx) {
    try {
        return Thread.backtrace(ctx, Backtracer.ACCURATE).map(function(a) {
            var s = DebugSymbol.fromAddress(a);
            return a + " " + (s.moduleName || "?") + "!" + (s.name || "?");
        });
    } catch (e) { return ["<bt_err:" + e + ">"]; }
}

function resolveSym(n) {
    try { if (Module.getGlobalExportByName) return Module.getGlobalExportByName(n); } catch (_) {}
    try { if (Module.findExportByName) return Module.findExportByName(null, n); } catch (_) {}
    return null;
}

// libc exit traps
(function installNativeTraps() {
    var names = ["_exit","exit","_Exit","abort","pthread_exit","raise","kill","tgkill","tkill"];
    for (var i = 0; i < names.length; i++) {
        var n = names[i];
        var a = resolveSym(n);
        if (!a) continue;
        Interceptor.attach(a, {
            onEnter: function (args) {
                send({ type: "native_exit", fn: n, tid: Process.getCurrentThreadId(), bt: btStrings(this.context) });
            }
        });
        send({ type: "trap_installed", fn: n });
    }
    var sc = resolveSym("syscall");
    if (sc) {
        Interceptor.attach(sc, {
            onEnter: function (args) {
                var no = args[0].toInt32();
                if (no === 93 || no === 94 || no === 129 || no === 130 || no === 131) {
                    send({ type: "native_exit", fn: "syscall(" + no + ")", tid: Process.getCurrentThreadId(), bt: btStrings(this.context) });
                }
            }
        });
        send({ type: "trap_installed", fn: "syscall" });
    }
})();

send({ type: "java_available", val: (typeof Java !== "undefined") ? "yes" : "no" });
if (typeof Java === "undefined") {
    send({ type: "java_skipped" });
} else
// Java traps
Java.perform(function () {
    try {
        var System = Java.use("java.lang.System");
        System.exit.implementation = function (code) {
            send({ type: "java_exit", fn: "System.exit", code: code, stack: Java.use("android.util.Log").getStackTraceString(Java.use("java.lang.Exception").$new()) });
            return this.exit(code);
        };
        send({ type: "trap_installed", fn: "java.System.exit" });
    } catch (e) { send({ type: "hook_err", fn: "System.exit", err: String(e) }); }

    try {
        var Runtime = Java.use("java.lang.Runtime");
        Runtime.exit.implementation = function (code) {
            send({ type: "java_exit", fn: "Runtime.exit", code: code, stack: Java.use("android.util.Log").getStackTraceString(Java.use("java.lang.Exception").$new()) });
            return this.exit(code);
        };
        send({ type: "trap_installed", fn: "java.Runtime.exit" });
    } catch (e) { send({ type: "hook_err", fn: "Runtime.exit", err: String(e) }); }

    try {
        var Proc = Java.use("android.os.Process");
        Proc.killProcess.implementation = function (pid) {
            send({ type: "java_exit", fn: "Process.killProcess", pid: pid, stack: Java.use("android.util.Log").getStackTraceString(Java.use("java.lang.Exception").$new()) });
            return this.killProcess(pid);
        };
        send({ type: "trap_installed", fn: "Process.killProcess" });
    } catch (e) { send({ type: "hook_err", fn: "Process.killProcess", err: String(e) }); }

    try {
        var Activity = Java.use("android.app.Activity");
        Activity.finishAndRemoveTask.implementation = function () {
            send({ type: "java_exit", fn: "Activity.finishAndRemoveTask", stack: Java.use("android.util.Log").getStackTraceString(Java.use("java.lang.Exception").$new()) });
            return this.finishAndRemoveTask();
        };
        send({ type: "trap_installed", fn: "Activity.finishAndRemoveTask" });
    } catch (e) { send({ type: "hook_err", fn: "finishAndRemoveTask", err: String(e) }); }

    try {
        var Activity2 = Java.use("android.app.Activity");
        Activity2.finish.implementation = function () {
            send({ type: "java_exit", fn: "Activity.finish", stack: Java.use("android.util.Log").getStackTraceString(Java.use("java.lang.Exception").$new()) });
            return this.finish();
        };
        send({ type: "trap_installed", fn: "Activity.finish" });
    } catch (e) { send({ type: "hook_err", fn: "Activity.finish", err: String(e) }); }
});

send({ type: "ready" });
"""


def main() -> int:
    mgr = frida.get_device_manager()
    dev = mgr.add_remote_device(HOST)
    procs = None
    t_start = time.time()
    while time.time() - t_start < 15:
        try:
            procs = dev.enumerate_processes()
            if procs:
                break
        except Exception:
            pass
        time.sleep(0.1)
    if not procs:
        print("[!] no gadget", flush=True)
        return 1
    print(f"[*] attach pid={procs[0].pid}", flush=True)
    session = dev.attach(procs[0].pid)
    script = session.create_script(JS_HOOK)

    def on_message(msg, _data):
        if msg["type"] == "send":
            p = msg.get("payload") or {}
            t = p.get("type")
            if t == "java_exit":
                print(f"\n### JAVA EXIT: {p['fn']}", flush=True)
                print(p.get("stack", ""), flush=True)
            elif t == "native_exit":
                print(f"\n### NATIVE EXIT: {p['fn']} tid={p['tid']}", flush=True)
                for i, frame in enumerate(p.get("bt", [])):
                    print(f"  #{i:<2} {frame}", flush=True)
            elif t in ("trap_installed", "ready", "hook_err"):
                print(f"[msg] {p}", flush=True)
        elif msg["type"] == "error":
            print(f"[ERROR] {msg}", flush=True)

    script.on("message", on_message)
    script.load()
    time.sleep(0.3)
    print("[*] traps loaded, watching...", flush=True)
    t0 = time.time()
    is_dead = {"v": False}
    def on_detached(*a):
        elapsed = time.time() - t0
        print(f"[{elapsed:5.1f}s] DEAD/DETACHED {a}", flush=True)
        is_dead["v"] = True
    session.on("detached", on_detached)
    while not is_dead["v"]:
        time.sleep(0.5)
    time.sleep(1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
