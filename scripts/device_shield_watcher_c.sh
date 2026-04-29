#!/system/bin/sh
# Watcher that invokes the native shield_patcher binary.
# Args: $1 = timeout seconds (default 30), $2 = interval sec (default 0.05)
set -u
TIMEOUT="${1:-30}"
INTERVAL="${2:-0.05}"
PATCHER=/data/local/tmp/shield_patcher
LOG=/data/local/tmp/ushield_watch.log

start=$(date +%s)
last_pid=""
attempts=0
: > "$LOG"

while :; do
    now=$(date +%s)
    el=$(( now - start ))
    if [ "$el" -ge "$TIMEOUT" ]; then
        echo "TIMEOUT after $el s attempts=$attempts" | tee -a "$LOG"
        exit 1
    fi

    pid=$(pidof com.cygames.umamusume 2>/dev/null | awk '{print $1}')
    if [ -z "$pid" ]; then sleep "$INTERVAL"; continue; fi
    if [ "$pid" != "$last_pid" ]; then
        echo "PID $last_pid -> $pid at t+${el}s" | tee -a "$LOG"
        last_pid="$pid"
    fi

    attempts=$((attempts + 1))
    out=$("$PATCHER" "$pid" 2>&1)
    rc=$?
    echo "[a$attempts t+${el}s pid=$pid rc=$rc] $out" >> "$LOG"
    if [ "$rc" = 0 ]; then
        echo "PATCHED $out" | tee -a "$LOG"
        exit 0
    fi
    sleep "$INTERVAL"
done
