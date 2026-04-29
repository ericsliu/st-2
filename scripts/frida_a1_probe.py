#!/usr/bin/env python3
"""Branch A1 probe (PACKET_INTERCEPTION_SPEC_ADDENDUM_3).

Reliably attach to the MAIN Uma Musume activity via ZygiskFrida gadget,
install Java-side quit hooks the moment Java is available, and log every
agent message so we can see WHY Hachimi dies at t+~2s.

Flow:
  1. force-stop umamusume
  2. launch via monkey
  3. poll for the main activity pid (via /proc/<pid>/cmdline == exact package name)
  4. enumerate frida gadgets, match against pid
  5. attach, load hook script
  6. tail every message until session dies or timeout
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import frida

DEVICE = "127.0.0.1:5555"
PACKAGE = "com.cygames.umamusume"
GADGET_HOST = "127.0.0.1:27042"


HOOK_JS = r"""
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
function javaBt() {
    try {
        var ex = Java.use("java.lang.Exception").$new();
        var s = Java.use("android.util.Log").getStackTraceString(ex);
        return s.split("\n").map(function(l) { return l.trim(); }).filter(Boolean);
    } catch (e) { return ["<java_bt_err:" + e + ">"]; }
}

(function nativeTraps() {
    var names = ["_exit","exit","_Exit","abort","pthread_exit","raise","kill","tgkill","tkill"];
    names.forEach(function (n) {
        var a = resolveSym(n);
        if (!a) return;
        Interceptor.attach(a, {
            onEnter: function (args) {
                send({
                    type: "native_exit",
                    fn: n,
                    tid: Process.getCurrentThreadId(),
                    arg0: args[0].toInt32(),
                    bt: btStrings(this.context)
                });
            }
        });
        send({ type: "native_trap_installed", fn: n });
    });
    var sc = resolveSym("syscall");
    if (sc) {
        Interceptor.attach(sc, {
            onEnter: function (args) {
                var no = args[0].toInt32();
                if ([93,94,129,130,131].indexOf(no) >= 0) {
                    send({
                        type: "native_exit",
                        fn: "syscall(" + no + ")",
                        tid: Process.getCurrentThreadId(),
                        bt: btStrings(this.context)
                    });
                }
            }
        });
        send({ type: "native_trap_installed", fn: "syscall" });
    }
})();

send({ type: "java_probe_start", java_defined: (typeof Java !== "undefined") });

function installJavaTraps() {
    try {
        var System = Java.use("java.lang.System");
        System.exit.implementation = function (code) {
            send({ type: "java_exit", fn: "System.exit", code: code, bt: javaBt() });
            return this.exit(code);
        };
        send({ type: "java_trap_installed", fn: "System.exit" });
    } catch (e) { send({ type: "java_trap_err", fn: "System.exit", err: String(e) }); }

    try {
        var Runtime = Java.use("java.lang.Runtime");
        Runtime.exit.implementation = function (code) {
            send({ type: "java_exit", fn: "Runtime.exit", code: code, bt: javaBt() });
            return this.exit(code);
        };
        Runtime.halt.implementation = function (code) {
            send({ type: "java_exit", fn: "Runtime.halt", code: code, bt: javaBt() });
            return this.halt(code);
        };
        send({ type: "java_trap_installed", fn: "Runtime.exit+halt" });
    } catch (e) { send({ type: "java_trap_err", fn: "Runtime", err: String(e) }); }

    try {
        var Proc = Java.use("android.os.Process");
        Proc.killProcess.implementation = function (pid) {
            send({ type: "java_exit", fn: "Process.killProcess", pid: pid, bt: javaBt() });
            return this.killProcess(pid);
        };
        Proc.sendSignal.implementation = function (pid, sig) {
            send({ type: "java_exit", fn: "Process.sendSignal", pid: pid, sig: sig, bt: javaBt() });
            return this.sendSignal(pid, sig);
        };
        send({ type: "java_trap_installed", fn: "Process.killProcess+sendSignal" });
    } catch (e) { send({ type: "java_trap_err", fn: "Process", err: String(e) }); }

    try {
        var Act = Java.use("android.app.Activity");
        Act.finish.implementation = function () {
            send({ type: "java_exit", fn: "Activity.finish", cls: this.getClass().getName(), bt: javaBt() });
            return this.finish();
        };
        Act.finishAffinity.implementation = function () {
            send({ type: "java_exit", fn: "Activity.finishAffinity", cls: this.getClass().getName(), bt: javaBt() });
            return this.finishAffinity();
        };
        Act.finishAndRemoveTask.implementation = function () {
            send({ type: "java_exit", fn: "Activity.finishAndRemoveTask", cls: this.getClass().getName(), bt: javaBt() });
            return this.finishAndRemoveTask();
        };
        send({ type: "java_trap_installed", fn: "Activity.finish+finishAffinity+finishAndRemoveTask" });
    } catch (e) { send({ type: "java_trap_err", fn: "Activity", err: String(e) }); }
}

if (typeof Java === "undefined") {
    send({ type: "java_poll_start" });
    // Poll briefly in case Java loads after the agent.
    var attempts = 0;
    var iv = setInterval(function () {
        attempts++;
        if (typeof Java !== "undefined") {
            clearInterval(iv);
            send({ type: "java_ready", attempts: attempts });
            Java.perform(installJavaTraps);
        } else if (attempts >= 200) {
            clearInterval(iv);
            send({ type: "java_never_ready" });
        }
    }, 50);
} else {
    Java.perform(installJavaTraps);
}

send({ type: "ready" });
"""


def adb(*args: str, check: bool = True) -> str:
    r = subprocess.run(["adb", "-s", DEVICE, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {args}: rc={r.returncode} stderr={r.stderr}")
    return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--duration", type=float, default=30.0, help="Watch seconds after attach")
    ap.add_argument("--startup-wait", type=float, default=6.0, help="Seconds to wait after monkey launch for main pid")
    ap.add_argument("--no-launch", action="store_true", help="Skip launch; attach to already-running Uma")
    args = ap.parse_args()

    if not args.no_launch:
        print("[*] force-stop Uma")
        adb("shell", f"am force-stop {PACKAGE}", check=False)
        time.sleep(0.5)
        print("[*] monkey launch")
        adb("shell", f"monkey -p {PACKAGE} -c android.intent.category.LAUNCHER 1", check=False)

    # Poll for the main-activity pid (exact cmdline match)
    deadline = time.monotonic() + args.startup_wait
    main_pid = None
    while time.monotonic() < deadline:
        out = adb("shell", f"pidof {PACKAGE}", check=False).strip()
        pids = [int(p) for p in out.split() if p.isdigit()]
        for pid in pids:
            cmd = adb("shell", f"cat /proc/{pid}/cmdline 2>/dev/null", check=False).replace("\x00", " ").strip()
            first = cmd.split()[0] if cmd else ""
            if first == PACKAGE:
                main_pid = pid
                break
        if main_pid is not None:
            break
        time.sleep(0.25)

    if main_pid is None:
        print("[!] no main Uma pid found within startup window", file=sys.stderr)
        return 2
    print(f"[*] main Uma pid = {main_pid}")

    # Port forward + wait for gadget listener
    adb("forward", f"tcp:27042", f"tcp:27042", check=False)

    mgr = frida.get_device_manager()
    dev = None
    t0 = time.time()
    while time.time() - t0 < 12:
        try:
            dev = mgr.add_remote_device(GADGET_HOST)
            procs = dev.enumerate_processes()
            if procs:
                break
        except Exception:
            pass
        try:
            mgr.remove_remote_device(GADGET_HOST)
        except Exception:
            pass
        time.sleep(0.2)
    if not dev:
        print("[!] gadget never appeared", file=sys.stderr)
        return 3

    procs = dev.enumerate_processes()
    print(f"[*] gadget procs: {[(p.pid, p.name) for p in procs]}")
    # Gadget reports the host pid it was injected into as its pid.
    match = [p for p in procs if p.pid == main_pid]
    if not match:
        print(f"[!] no gadget attached to main pid {main_pid}; available: {[p.pid for p in procs]}", file=sys.stderr)
        # Fall back to first gadget but log warning.
        if not procs:
            return 4
        match = [procs[0]]
        print(f"[*] FALLBACK: attaching to pid={match[0].pid}")

    session = dev.attach(match[0].pid)
    attach_t = time.time()

    died = {"v": False}
    def on_detached(*a):
        elapsed = time.time() - attach_t
        print(f"[{elapsed:5.1f}s] SESSION DETACHED {a}", flush=True)
        died["v"] = True
    session.on("detached", on_detached)

    script = session.create_script(HOOK_JS)

    msg_count = {"n": 0}
    def on_message(msg, data):
        msg_count["n"] += 1
        t_off = time.time() - attach_t
        if msg["type"] == "send":
            p = msg.get("payload") or {}
            t = p.get("type", "?")
            if t in ("java_exit", "native_exit"):
                print(f"\n### [{t_off:5.1f}s] *** {t.upper()}: {p.get('fn')} ***", flush=True)
                for k, v in p.items():
                    if k in ("type", "fn", "bt"):
                        continue
                    print(f"    {k}: {v}", flush=True)
                bt = p.get("bt") or []
                for i, frame in enumerate(bt[:30]):
                    print(f"    #{i:<2} {frame}", flush=True)
            else:
                print(f"[{t_off:5.1f}s] [msg] {p}", flush=True)
        elif msg["type"] == "error":
            print(f"[{t_off:5.1f}s] [ERROR] {msg.get('stack', msg)}", flush=True)
    script.on("message", on_message)
    script.load()
    print(f"[{time.time()-attach_t:.1f}s] script.load() done; watching for {args.duration}s or death", flush=True)

    end = time.time() + args.duration
    while time.time() < end and not died["v"]:
        time.sleep(0.2)
    time.sleep(0.5)
    print(f"\n[summary] messages={msg_count['n']}  died_early={died['v']}")
    try:
        session.detach()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
