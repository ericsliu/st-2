#!/usr/bin/env bash
# Push frida-server to the MuMu emulator (if missing), launch it as root,
# and wait for the default Frida port (27042) to be listening.
#
# Assumes: MuMu is running at 127.0.0.1:5555 with root enabled, and
# the host frida + frida-tools are installed in .venv (matching version).

set -euo pipefail

DEVICE=${DEVICE:-127.0.0.1:5555}
FRIDA_VERSION=${FRIDA_VERSION:-17.9.1}
LOCAL_BINARY=${LOCAL_BINARY:-/tmp/frida_work/server/frida-server-${FRIDA_VERSION}-android-arm64}
REMOTE_PATH=/data/local/tmp/frida-server

# Push binary if missing on device
if ! adb -s "$DEVICE" shell "[ -x $REMOTE_PATH ]"; then
    echo "[frida_start] pushing $LOCAL_BINARY -> $REMOTE_PATH"
    adb -s "$DEVICE" push "$LOCAL_BINARY" "$REMOTE_PATH" >/dev/null
    adb -s "$DEVICE" shell "chmod 755 $REMOTE_PATH"
fi

# Kill any existing frida-server
adb -s "$DEVICE" shell "pkill -f frida-server" 2>/dev/null || true

# Launch backgrounded (MuMu adb shell is already root, no su needed)
adb -s "$DEVICE" shell "nohup $REMOTE_PATH >/dev/null 2>&1 &"

# Wait for the port to respond
for i in $(seq 1 20); do
    if adb -s "$DEVICE" shell "pgrep -f frida-server" >/dev/null 2>&1; then
        echo "[frida_start] ready (pid $(adb -s "$DEVICE" shell "pgrep -f frida-server" | tr -d '\r'))"
        exit 0
    fi
    sleep 0.25
done

echo "[frida_start] ERROR: frida-server did not start within 5s" >&2
exit 1
