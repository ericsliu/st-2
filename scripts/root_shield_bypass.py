#!/usr/bin/env python3
"""Launch Uma Global with root-based shield bypass (no Frida).

Steps:
 1. Force-stop com.cygames.umamusume.
 2. Push device_shield_patch.sh to /data/local/tmp.
 3. Launch Uma via `am start`.
 4. Poll adb for Uma PID; once it appears, run the patcher under su
    every 250 ms until it succeeds or Uma exits.
 5. Tail logcat for Hachimi + shield crash signals so the operator
    knows whether the bypass beat the detection window.

No Frida trampolines involved — we rely entirely on /proc/<pid>/mem
writes from root.
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.abspath(os.path.dirname(__file__))
SCRIPT = os.path.join(HERE, "device_shield_patch.sh")
DEVICE_PATH = "/data/local/tmp/device_shield_patch.sh"
UMA_PKG = "com.cygames.umamusume"
UMA_ACT = f"{UMA_PKG}/com.cygames.umamusume.CompanionActivity"
UMA_LAUNCH_MONKEY = ["adb", "shell", "monkey", "-p", UMA_PKG,
                     "-c", "android.intent.category.LAUNCHER", "1"]


def adb(*args, capture=True, timeout=15):
    cmd = ["adb", *args]
    if capture:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    subprocess.run(cmd, timeout=timeout, check=False)
    return 0, "", ""


def get_uma_pid():
    rc, out, _ = adb("shell", f"pidof {UMA_PKG}")
    if rc != 0 or not out.strip():
        return None
    # First pid is usually the main process; confirm via /proc/<pid>/comm.
    pids = out.strip().split()
    for p in pids:
        rc2, comm, _ = adb("shell", f"cat /proc/{p}/comm 2>/dev/null")
        if rc2 == 0 and comm.strip().endswith("umamusume"):
            return p
    return pids[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=60.0,
                    help="seconds to keep trying the patcher")
    ap.add_argument("--interval", type=float, default=0.25,
                    help="seconds between patch attempts")
    ap.add_argument("--skip-launch", action="store_true",
                    help="assume Uma is already running; just patch")
    ap.add_argument("--keep-going", action="store_true",
                    help="stay running and tail logcat after patch")
    args = ap.parse_args()

    if not args.skip_launch:
        print("[*] force-stopping Uma")
        adb("shell", f"am force-stop {UMA_PKG}")
        time.sleep(0.5)

    print("[*] pushing patcher script")
    rc, out, err = adb("push", SCRIPT, DEVICE_PATH)
    if rc != 0:
        print(f"[!] push failed: {err}", file=sys.stderr)
        return 1
    adb("shell", f"chmod 755 {DEVICE_PATH}")

    if not args.skip_launch:
        print("[*] launching Uma via monkey")
        adb(*UMA_LAUNCH_MONKEY[1:])

    t0 = time.time()
    pid = None
    while time.time() - t0 < args.timeout:
        pid = get_uma_pid()
        if pid:
            break
        time.sleep(0.1)
    if not pid:
        print(f"[!] Uma PID did not appear within {args.timeout}s", file=sys.stderr)
        return 2
    print(f"[*] Uma PID: {pid}")

    # Tight patch loop until success or Uma exits.
    patched = False
    attempt = 0
    while time.time() - t0 < args.timeout:
        attempt += 1
        rc, out, err = adb("shell",
                           f"/system/bin/magisk su -c '{DEVICE_PATH} {pid}'",
                           timeout=30)
        print(f"[*] attempt {attempt} rc={rc}")
        for line in (out + err).splitlines():
            print(f"      {line}")
        if rc == 0 and "OK patches confirmed" in out:
            patched = True
            break
        # Was the pid still alive?
        cur_pid = get_uma_pid()
        if cur_pid != pid:
            print(f"[!] Uma pid changed: {pid} -> {cur_pid}", file=sys.stderr)
            if cur_pid:
                pid = cur_pid
            else:
                return 3
        time.sleep(args.interval)

    if not patched:
        print("[!] patch did not succeed in timeout window", file=sys.stderr)
        return 4

    print(f"[+] shield patched after {time.time()-t0:.2f}s")

    if not args.keep_going:
        return 0

    print("[*] tailing logcat for 60s (Uma, Hachimi, Zygote)")
    proc = subprocess.Popen(
        ["adb", "logcat", "-v", "brief", "-T", "1",
         "Hachimi:V", "Zygote:V", "ActivityManager:V", "DEBUG:V",
         f"*:S"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    t1 = time.time()
    try:
        while time.time() - t1 < 60.0:
            line = proc.stdout.readline()
            if not line:
                break
            print(line.rstrip())
    finally:
        proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
