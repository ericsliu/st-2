#!/system/bin/sh
# Launch Uma, wait for Hachimi's "Hooking finished" line in logcat,
# THEN run the shield_watchdog. Does delaying the patch keep Uma alive?
set -u
POLL_MS="${1:-50}"
TOTAL="${2:-30}"
LOG=/data/local/tmp/ushield_after_hooks.log
: > "$LOG"

am force-stop com.cygames.umamusume 2>/dev/null
sleep 0.5
logcat -c
monkey -p com.cygames.umamusume -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
echo "LAUNCHED t=$(date +%H:%M:%S.%N | cut -c1-12)" | tee -a "$LOG"

# Wait up to 10s for "Hooking finished" from Hachimi
pid=""
for i in $(seq 1 200); do
    if logcat -d -v time Hachimi:V *:S 2>/dev/null | grep -q "Hooking finished"; then
        line=$(logcat -d -v time Hachimi:V *:S 2>/dev/null | grep "Hooking finished" | tail -1)
        # Extract the integer PID between "( " and " )"
        # busybox-safe PID extraction: grab first (nnn) group
        pid=$(echo "$line" | awk -F'[()]' '{print $2}' | awk '{print $1}')
        echo "HOOKS_FINISHED t=$(date +%H:%M:%S.%N | cut -c1-12) pid=$pid line=[$line]" | tee -a "$LOG"
        break
    fi
    sleep 0.05
done
if [ -z "$pid" ]; then
    echo "HOOKING_NEVER_FINISHED" | tee -a "$LOG"
    exit 2
fi

# Verify pid still alive
if ! kill -0 "$pid" 2>/dev/null; then
    echo "PID_DEAD_BEFORE_PATCH pid=$pid" | tee -a "$LOG"
    exit 3
fi

# Run watchdog
/data/local/tmp/shield_watchdog "$pid" "$POLL_MS" "$TOTAL" 2>&1 | tee -a "$LOG"
echo "END t=$(date +%H:%M:%S.%N | cut -c1-12)" | tee -a "$LOG"
