#!/usr/bin/env python3
"""Rename Frida threads via /proc then install ONE Interceptor.attach.

Tests the shield's detection path:
  - baseline (do nothing): Uma survives ~25s
  - rename_only (rename threads, no hooks): should also survive if shield
    detects hooks not names
  - rename_and_hook (rename + one Interceptor.attach): the real test. If
    Uma dies, hooks alone are the trigger (renaming doesn't help).
    If Uma survives, renaming bypasses the detection.
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

SCRIPT = r"""
var RUN_MODE = "__MODE__";  // "inert", "rename_only", "rename_and_hook", "hook_only"

function listTaskDir() {
    // /proc/self/task is a directory of TIDs. File.open doesn't read dirs,
    // so we use opendir via Module.getGlobalExportByName if possible. But
    // simplest: use NativeFunction over opendir/readdir from libc.
    var tids = [];
    try {
        var opendir = new NativeFunction(Module.getGlobalExportByName("opendir"), "pointer", ["pointer"]);
        var readdir = new NativeFunction(Module.getGlobalExportByName("readdir"), "pointer", ["pointer"]);
        var closedir = new NativeFunction(Module.getGlobalExportByName("closedir"), "int", ["pointer"]);
        var pathStr = Memory.allocUtf8String("/proc/self/task");
        var dir = opendir(pathStr);
        if (dir.isNull()) return tids;
        while (true) {
            var ent = readdir(dir);
            if (ent.isNull()) break;
            // struct dirent: u64 d_ino, s64 d_off, u16 d_reclen, u8 d_type, char d_name[256]
            var nameOff = 8 + 8 + 2 + 1;  // 19; but padding makes it 19, aligned — on arm64, d_name is at offset 19
            var name = ent.add(nameOff).readCString();
            if (!name) continue;
            if (name === "." || name === "..") continue;
            var tid = parseInt(name, 10);
            if (!isNaN(tid)) tids.push(tid);
        }
        closedir(dir);
    } catch (e) {
        send({ type: "taskdir_err", err: String(e) });
    }
    return tids;
}

function readComm(tid) {
    try {
        var f = new File("/proc/self/task/" + tid + "/comm", "r");
        var s = f.readLine();
        f.close();
        return s.replace(/[\r\n\x00]/g, "").trim();
    } catch (e) { return null; }
}
function writeComm(tid, name) {
    try {
        var f = new File("/proc/self/task/" + tid + "/comm", "w");
        f.write(name.slice(0, 15));
        f.close();
        return true;
    } catch (e) { return false; }
}
var PATTERNS = [/^gum-/, /^gmain$/, /^gdbus$/, /^pool-frida/, /^pool-spawn/, /^pool-gum-js/, /^frida-/];
function isFrida(n) { for (var i = 0; i < PATTERNS.length; i++) if (PATTERNS[i].test(n)) return true; return false; }

function sweepAndRename() {
    var tids = listTaskDir();
    var renamed = [];
    var fridaHits = [];
    for (var i = 0; i < tids.length; i++) {
        var tid = tids[i];
        var name = readComm(tid);
        if (!name) continue;
        if (isFrida(name)) {
            fridaHits.push({ tid: tid, name: name });
            var newName = "Thread-JVM-" + i;
            if (writeComm(tid, newName)) {
                renamed.push({ tid: tid, from: name, to: newName });
            }
        }
    }
    send({ type: "sweep", total: tids.length, frida: fridaHits.length, renamed: renamed });
}

if (RUN_MODE === "rename_only" || RUN_MODE === "rename_and_hook") {
    sweepAndRename();
    // Re-sweep in case threads are re-named by frida
    setTimeout(sweepAndRename, 200);
    setTimeout(sweepAndRename, 1000);
    setTimeout(sweepAndRename, 3000);
}

if (RUN_MODE === "rename_and_hook" || RUN_MODE === "hook_only") {
    try {
        var addr = Module.getGlobalExportByName("open");  // libc open(3)
        Interceptor.attach(addr, {
            onEnter: function (args) { /* no-op */ }
        });
        send({ type: "hook_installed", fn: "open", addr: addr.toString() });
    } catch (e) {
        send({ type: "hook_err", err: String(e) });
    }
}

send({ type: "loaded", mode: RUN_MODE });
"""


def adb(*args: str, check: bool = True) -> str:
    r = subprocess.run(["adb", "-s", DEVICE, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {args}: rc={r.returncode} stderr={r.stderr}")
    return r.stdout


def run_probe(mode: str, duration: float, no_launch: bool) -> dict:
    if not no_launch:
        adb("shell", f"am force-stop {PACKAGE}", check=False)
        time.sleep(0.5)
        adb("shell", f"monkey -p {PACKAGE} -c android.intent.category.LAUNCHER 1", check=False)
        deadline = time.monotonic() + 8
        main_pid = None
        while time.monotonic() < deadline:
            out = adb("shell", f"pidof {PACKAGE}", check=False).strip()
            pids = [int(p) for p in out.split() if p.isdigit()]
            for pid in pids:
                cmd = adb("shell", f"cat /proc/{pid}/cmdline 2>/dev/null", check=False).replace("\x00", " ").strip()
                if cmd.split()[0:1] == [PACKAGE]:
                    main_pid = pid
                    break
            if main_pid:
                break
            time.sleep(0.25)
    else:
        out = adb("shell", f"pidof {PACKAGE}", check=False).strip()
        pids = [int(p) for p in out.split() if p.isdigit()]
        main_pid = pids[0] if pids else None

    if not main_pid:
        return {"mode": mode, "error": "no_pid"}

    adb("forward", "tcp:27042", "tcp:27042", check=False)
    mgr = frida.get_device_manager()
    dev = None
    t0 = time.time()
    while time.time() - t0 < 12:
        try:
            dev = mgr.add_remote_device(GADGET_HOST)
            if dev.enumerate_processes():
                break
        except Exception:
            pass
        try:
            mgr.remove_remote_device(GADGET_HOST)
        except Exception:
            pass
        time.sleep(0.2)
    if not dev:
        return {"mode": mode, "error": "no_gadget"}

    procs = dev.enumerate_processes()
    match = [p for p in procs if p.pid == main_pid] or procs[:1]
    session = dev.attach(match[0].pid)
    attach_t = time.time()

    died = {"v": False, "reason": None}
    def on_det(*a):
        died["v"] = True
        died["reason"] = str(a)
    session.on("detached", on_det)

    def on_msg(msg, data):
        t = time.time() - attach_t
        if msg["type"] == "send":
            p = msg.get("payload") or {}
            print(f"[{mode}][{t:5.1f}s] {p}", flush=True)
        elif msg["type"] == "error":
            print(f"[{mode}][{t:5.1f}s] ERR {msg.get('stack', msg)}", flush=True)

    script_src = SCRIPT.replace("__MODE__", mode)
    script = session.create_script(script_src)
    script.on("message", on_msg)
    script.load()

    end = time.time() + duration
    while time.time() < end and not died["v"]:
        time.sleep(0.2)
    elapsed = time.time() - attach_t
    result = {"mode": mode, "elapsed": elapsed, "died": died["v"], "reason": died["reason"]}
    try:
        session.detach()
    except Exception:
        pass
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--modes", nargs="+", default=["inert", "hook_only", "rename_and_hook"])
    ap.add_argument("--duration", type=float, default=15.0)
    args = ap.parse_args()

    results = []
    for m in args.modes:
        print(f"\n========== MODE={m} ==========", flush=True)
        r = run_probe(m, args.duration, no_launch=False)
        results.append(r)
        print(f"[result] {r}", flush=True)
        time.sleep(1.0)

    print("\n========== SUMMARY ==========")
    for r in results:
        print(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
