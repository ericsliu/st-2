#!/system/bin/sh
# On-device watcher: polls for Uma PID then applies shield patch via
# device_shield_patch.sh repeatedly until success or timeout. Eliminates
# adb round-trip overhead of a host-side polling loop.
#
# Args: $1 = seconds to keep polling (default 30)
#       $2 = interval seconds (default 0.1)
# Exit: 0 = patched, 1 = timeout, 2 = never saw uma pid

set -u

TIMEOUT="${1:-30}"
INTERVAL="${2:-0.1}"
PATCHER=/data/local/tmp/device_shield_patch.sh
LOG=/data/local/tmp/ushield_watch.log

start=$(date +%s)
last_pid=""
attempts=0
: > "$LOG"

while :; do
    now=$(date +%s)
    elapsed=$(( now - start ))
    if [ "$elapsed" -ge "$TIMEOUT" ]; then
        echo "TIMEOUT after $elapsed s, attempts=$attempts" | tee -a "$LOG"
        if [ -z "$last_pid" ]; then exit 2; fi
        exit 1
    fi

    pid=$(pidof com.cygames.umamusume 2>/dev/null | awk '{print $1}')
    if [ -z "$pid" ]; then
        sleep "$INTERVAL"
        continue
    fi
    if [ "$pid" != "$last_pid" ]; then
        echo "PID_CHANGE $last_pid -> $pid at t+${elapsed}s" | tee -a "$LOG"
        last_pid="$pid"
    fi

    attempts=$((attempts + 1))
    out=$("$PATCHER" "$pid" 2>&1)
    echo "[attempt $attempts t+${elapsed}s pid=$pid]" >> "$LOG"
    echo "$out" >> "$LOG"
    if echo "$out" | grep -q "OK patches confirmed"; then
        echo "PATCHED pid=$pid attempts=$attempts elapsed=${elapsed}s" | tee -a "$LOG"
        echo "$out" | grep -E 'FOUND|PATCHED|VERIFY|OK'
        exit 0
    fi
    sleep "$INTERVAL"
done
