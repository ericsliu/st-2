#!/system/bin/sh
# Launch Uma, wait for PID, attach shield_watchdog to observe
# whether the shield restores its writer/reader bytes after we patch.
#
# Args: $1 = poll_ms (default 50), $2 = total_sec (default 30)
set -u
POLL_MS="${1:-50}"
TOTAL="${2:-30}"
LOG=/data/local/tmp/ushield_watchdog.log

: > "$LOG"

# Force-stop any existing instance so we get a fresh launch
am force-stop com.cygames.umamusume 2>/dev/null

# Kick it
monkey -p com.cygames.umamusume -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1

# Find the MAIN Uma process (cmdline exactly com.cygames.umamusume),
# not the :tab1 subprocess.
find_main_pid() {
    for p in $(pidof com.cygames.umamusume 2>/dev/null); do
        cmd=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | awk '{print $1}')
        if [ "$cmd" = "com.cygames.umamusume" ]; then
            echo "$p"
            return
        fi
    done
}

stable_pid=""
for i in $(seq 1 200); do
    p=$(find_main_pid)
    if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
        stable_pid="$p"
        break
    fi
    sleep 0.1
done
if [ -z "$stable_pid" ]; then
    echo "NO_MAIN_PID" | tee -a "$LOG"
    exit 2
fi
echo "MAIN_PID=$stable_pid" | tee -a "$LOG"

# Retry find_shield until shield region is populated, then monitor.
attempt=0
while :; do
    attempt=$((attempt + 1))
    # Make sure the stable pid is still alive before invoking.
    if ! kill -0 "$stable_pid" 2>/dev/null; then
        echo "STABLE_PID_DIED attempt=$attempt" | tee -a "$LOG"
        exit 3
    fi
    out=$(/data/local/tmp/shield_watchdog "$stable_pid" "$POLL_MS" "$TOTAL" 2>&1)
    rc=$?
    echo "--- attempt=$attempt pid=$stable_pid rc=$rc ---" >> "$LOG"
    echo "$out" >> "$LOG"
    case "$out" in
        *shield_base=*)
            echo "$out"
            echo "EXIT rc=$rc" | tee -a "$LOG"
            exit "$rc"
            ;;
    esac
    sleep 0.1
    if [ "$attempt" -ge 200 ]; then
        echo "NEVER_FOUND attempts=$attempt" | tee -a "$LOG"
        exit 1
    fi
done
