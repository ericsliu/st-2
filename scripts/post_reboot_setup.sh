#!/bin/bash
# Post-reboot setup for BlueStacks Hachimi/shield bypass work.
# Run after BlueStacks has fully restarted.
#
# Idempotent: safe to run multiple times.

set -u

echo "[*] adb connect"
adb disconnect >/dev/null 2>&1
adb connect 127.0.0.1:5555

echo "[*] waiting for boot_completed"
for i in 1 2 3 4 5 6 7 8 9 10; do
    state=$(adb shell "getprop sys.boot_completed" 2>/dev/null | tr -d '\r')
    if [ "$state" = "1" ]; then echo "[+] booted"; break; fi
    sleep 3
done

echo "[*] verifying root dispatch (/data/local/tmp/su)"
if [ ! -e /dev/null ]; then :; fi
if ! adb shell "/data/local/tmp/su -c 'sh -c \"id\"'" 2>/dev/null | grep -q "uid=0"; then
    echo "[!] /data/local/tmp/su not yielding root. Trying recreate..."
    adb shell "ln -sf /sbin/magisk /data/local/tmp/su" || true
fi

echo "[*] removing /system/bin/su + /system/xbin/su (Magisk re-injects on every boot)"
adb shell "/data/local/tmp/su -c 'sh -c \"mount -o remount,rw /system/bin; rm -f /system/bin/su /system/xbin/su; mount -o remount,ro /system/bin\"'"

echo "[*] disabling Kitsune Mask package (io.github.huskydg.magisk)"
adb shell "/data/local/tmp/su -c 'sh -c \"pm disable-user --user 0 io.github.huskydg.magisk\"'" || true

echo "[*] post-check: su files"
adb shell "ls /system/bin/su /system/xbin/su 2>&1"

echo "[*] post-check: zygisk modules"
adb shell "/data/local/tmp/su -c 'sh -c \"ls /data/adb/modules/hachimi/disable /data/adb/modules/zygiskfrida/disable 2>&1\"'"

echo "[*] verifying patched ZygiskFrida gadget"
gadget=/data/local/tmp/re.zyg.fri/libgadget.so
if adb shell "ls $gadget" >/dev/null 2>&1; then
    if adb shell "strings $gadget 2>/dev/null | grep -q hidra-widget"; then
        echo "[+] patched gadget present (hidra-widget marker found)"
    else
        echo "[!] gadget present but appears UNPATCHED — re-run scripts/patch_gadget_strings.py"
    fi
else
    echo "[!] gadget missing at $gadget"
fi

echo "[*] pushing device scripts"
adb push /Users/eric/Documents/projects/st-2/scripts/device_shield_patch.sh /data/local/tmp/
adb push /Users/eric/Documents/projects/st-2/scripts/device_shield_watcher.sh /data/local/tmp/
adb push /Users/eric/Documents/projects/st-2/scripts/device_shield_diag.sh /data/local/tmp/
adb shell "chmod 755 /data/local/tmp/device_shield_patch.sh /data/local/tmp/device_shield_watcher.sh /data/local/tmp/device_shield_diag.sh"
echo "[+] setup complete — launch Uma Musume"
