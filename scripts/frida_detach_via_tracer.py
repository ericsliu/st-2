#!/usr/bin/env python3
"""Approach A: attach Frida to Uma's anti-debug ptracer child (TracerPid=0),
   have it call ptrace(PTRACE_DETACH, parent, 0, 0) to release the parent,
   then Frida-attach the parent.

Theory: the parent's watchdog kills the process when its tracer *dies*.
        If we leave the tracer alive but merely detached, maybe no kill."""
from __future__ import annotations
import subprocess, sys, time
import frida

DEVICE = "127.0.0.1:5555"
TARGET = "com.cygames.umamusume"
AGENT = "/Users/eric/Documents/projects/st-2/frida_agent/dist/agent.js"


def adb(*args):
    return subprocess.run(["adb", "-s", DEVICE, *args],
                          capture_output=True, text=True, check=True).stdout


def find_pids():
    out = adb("shell", f"pgrep -f {TARGET}")
    return sorted(int(x) for x in out.split() if x.strip().isdigit())


def tracer(pid):
    out = adb("shell", f"cat /proc/{pid}/status 2>/dev/null")
    for line in out.splitlines():
        if line.startswith("TracerPid:"):
            return int(line.split()[1])
    return -1


def main():
    pids = find_pids()
    print(f"[*] pids: {pids}")
    if len(pids) < 2:
        print("[!] need >=2 pids (parent + ptracer child)", file=sys.stderr)
        return 2
    parent = None
    ptracer = None
    for p in pids:
        tp = tracer(p)
        print(f"    pid={p}  TracerPid={tp}")
        if tp > 0:
            parent, ptracer = p, tp
    if parent is None:
        print("[!] no traced process found", file=sys.stderr)
        return 2
    print(f"[*] parent={parent}  ptracer={ptracer}")

    # ---- Attach Frida to the ptracer (TracerPid=0, should be allowed) ----
    device = frida.get_usb_device(timeout=5)
    print(f"[*] frida-attaching to ptracer pid {ptracer}")
    try:
        sess_t = device.attach(ptracer)
    except Exception as e:
        print(f"[!] attach to ptracer failed: {e}", file=sys.stderr)
        return 3

    # ---- Inject tiny script: call ptrace(PTRACE_DETACH=17, parent, 0, 0) ----
    src = r"""
    rpc.exports = {
        detachParent: function (parentPid) {
            var ptraceAddr = Module.findExportByName(null, 'ptrace');
            if (!ptraceAddr) return { ok: false, err: 'ptrace_not_found' };
            var ptraceFn = new NativeFunction(
                ptraceAddr, 'long', ['int', 'int', 'pointer', 'pointer']);
            // PTRACE_DETACH = 17 on arm64 linux
            var rc = ptraceFn(17, parentPid, NULL, NULL);
            return { ok: rc.toInt32() === 0, rc: rc.toInt32(),
                     errno: (rc.toInt32() < 0) ? 'see_strerror' : null };
        }
    };
    send({type: 'ready'});
    """
    script_t = sess_t.create_script(src)
    msgs = []
    script_t.on("message", lambda m, d: msgs.append(m))
    script_t.load()
    print(f"[*] ptracer-side script loaded; calling detach({parent})")
    result = script_t.exports_sync.detach_parent(parent)
    print(f"[*] detach rpc result: {result}")
    print(f"[*] script messages: {msgs}")

    time.sleep(0.4)

    # ---- Re-check TracerPid on parent ----
    tp = tracer(parent)
    print(f"[*] parent TracerPid now: {tp}")
    if tp == 0:
        print("[*] TracerPid cleared — parent is detached and alive!")
    elif tp < 0:
        print("[!] parent is gone (process dead)", file=sys.stderr)
        return 4
    else:
        print(f"[!] TracerPid still {tp}; detach didn't take", file=sys.stderr)
        return 5

    # ---- Try Frida-attaching the parent now ----
    print(f"[*] frida-attaching to parent pid {parent}")
    try:
        sess_p = device.attach(parent)
    except Exception as e:
        print(f"[!] parent attach failed: {e}", file=sys.stderr)
        return 6

    with open(AGENT) as f:
        code = f.read()
    script_p = sess_p.create_script(code)
    p_msgs = []
    script_p.on("message", lambda m, d: p_msgs.append(m))
    script_p.load()
    time.sleep(0.4)
    print(f"[*] ping: {script_p.exports_sync.ping()}")
    modules = script_p.exports_sync.report_modules(
        "libnative|libmain|libil2cpp|lib__4e06__|libunity")
    print(f"[*] interesting modules: {len(modules)}")
    for m in modules:
        print(f"    - {m['name']:<22} base={m['base']:>14} size={m['size']:>10}")
    lz4 = script_p.exports_sync.find_lz4_candidates()
    print(f"[*] lz4 candidates: {len(lz4)}")
    for c in lz4:
        print(f"    - {c['name']:<40} @ {c['address']}")
    print(f"[*] agent messages: {p_msgs[:10]}")
    print("[*] SUCCESS — attached. Detaching both sessions.")
    sess_p.detach()
    sess_t.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
