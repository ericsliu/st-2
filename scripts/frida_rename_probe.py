#!/usr/bin/env python3
"""Minimal shield-bypass test: rename Frida's threads immediately, do nothing else.

Inline Frida script (not the full agent). Enumerates threads, reads
/proc/self/task/<tid>/comm, renames any 'gum-*', 'pool-frida*', 'gmain',
'gdbus', etc. to 'Thread-ART-N'. Then sleeps.

If Uma survives significantly longer than the 1-4s seen before, the
CrackProof detection is by thread name, and C1 is unblocked (just need
to bake renaming into the main agent's startup).
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

function sweep(tag) {
    var threads = Process.enumerateThreads();
    var hits = [];
    var renamed = [];
    for (var i = 0; i < threads.length; i++) {
        var t = threads[i];
        var name = readComm(t.id);
        if (!name) continue;
        hits.push({ tid: t.id, name: name });
        if (isFrida(name)) {
            var newName = "Thread-ART-" + i;
            if (writeComm(t.id, newName)) {
                renamed.push({ tid: t.id, from: name, to: newName });
            }
        }
    }
    send({ type: "sweep", tag: tag, total: threads.length, renamed: renamed, allNames: hits });
}

// Immediate rename on load
sweep("initial");

// Re-sweep after 500ms in case Frida spawned more threads
setTimeout(function () { sweep("resweep_500ms"); }, 500);
setTimeout(function () { sweep("resweep_2s"); }, 2000);

send({ type: "done_loading" });
"""


def adb(*args: str, check: bool = True) -> str:
    r = subprocess.run(["adb", "-s", DEVICE, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {args}: rc={r.returncode} stderr={r.stderr}")
    return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--duration", type=float, default=20.0)
    ap.add_argument("--startup-wait", type=float, default=8.0)
    args = ap.parse_args()

    print("[*] force-stop Uma")
    adb("shell", f"am force-stop {PACKAGE}", check=False)
    time.sleep(0.5)
    print("[*] monkey launch")
    adb("shell", f"monkey -p {PACKAGE} -c android.intent.category.LAUNCHER 1", check=False)

    deadline = time.monotonic() + args.startup_wait
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

    if not main_pid:
        print("[!] main pid not found", file=sys.stderr)
        return 2
    print(f"[*] main pid = {main_pid}")

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
        print("[!] gadget not responding", file=sys.stderr)
        return 3

    procs = dev.enumerate_processes()
    match = [p for p in procs if p.pid == main_pid] or procs[:1]
    session = dev.attach(match[0].pid)
    attach_t = time.time()

    died = {"v": False}
    def on_det(*a):
        print(f"[{time.time()-attach_t:5.1f}s] DETACHED {a}", flush=True)
        died["v"] = True
    session.on("detached", on_det)

    def on_msg(msg, data):
        t = time.time() - attach_t
        if msg["type"] == "send":
            p = msg.get("payload") or {}
            if p.get("type") == "sweep":
                print(f"[{t:5.1f}s] SWEEP {p['tag']}: renamed={len(p['renamed'])}/{p['total']}", flush=True)
                for r in p["renamed"]:
                    print(f"           {r['tid']}: {r['from']!r} -> {r['to']!r}", flush=True)
                for h in (p.get("allNames") or [])[:40]:
                    print(f"             tid={h['tid']} name={h['name']!r}", flush=True)
            else:
                print(f"[{t:5.1f}s] {p}", flush=True)
        elif msg["type"] == "error":
            print(f"[{t:5.1f}s] ERR {msg.get('stack', msg)}", flush=True)

    script = session.create_script(SCRIPT)
    script.on("message", on_msg)
    script.load()
    print(f"[{time.time()-attach_t:.1f}s] script loaded; observing for {args.duration}s or death", flush=True)

    end = time.time() + args.duration
    while time.time() < end and not died["v"]:
        time.sleep(0.25)
    elapsed = time.time() - attach_t
    print(f"\n[summary] duration={elapsed:.1f}s died={died['v']}")
    try:
        session.detach()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
