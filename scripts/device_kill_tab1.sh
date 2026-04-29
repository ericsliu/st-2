#!/system/bin/sh
# Launch Uma; as soon as :tab1 subprocess appears, SIGSTOP it.
# See if main process stays alive longer than the usual ~2.7s-post-hooks death.
set -u
LOG=/data/local/tmp/tab1_kill.log
: > "$LOG"

am force-stop com.cygames.umamusume 2>/dev/null
sleep 0.5
logcat -c
echo "LAUNCH t=$(date +%H:%M:%S.%N | cut -c1-12)" | tee -a "$LOG"
monkey -p com.cygames.umamusume -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1

stopped=0
main_pid=""
tab1_pid=""
for i in $(seq 1 300); do
    if [ -z "$main_pid" ]; then
        for p in $(pidof com.cygames.umamusume 2>/dev/null); do
            c=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | awk '{print $1}')
            case "$c" in
                com.cygames.umamusume) main_pid="$p" ;;
                com.cygames.umamusume:tab1) tab1_pid="$p" ;;
            esac
        done
        if [ -n "$main_pid" ]; then echo "MAIN pid=$main_pid t=$(date +%H:%M:%S.%N | cut -c1-12)" | tee -a "$LOG"; fi
    fi
    if [ -z "$tab1_pid" ]; then
        for p in $(pidof com.cygames.umamusume 2>/dev/null); do
            c=$(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | awk '{print $1}')
            if [ "$c" = "com.cygames.umamusume:tab1" ]; then tab1_pid="$p"; fi
        done
        if [ -n "$tab1_pid" ]; then echo "TAB1 pid=$tab1_pid t=$(date +%H:%M:%S.%N | cut -c1-12)" | tee -a "$LOG"; fi
    fi
    if [ -n "$tab1_pid" ] && [ "$stopped" = 0 ]; then
        kill -STOP "$tab1_pid" 2>/dev/null
        rc=$?
        echo "SIGSTOP tab1 pid=$tab1_pid rc=$rc t=$(date +%H:%M:%S.%N | cut -c1-12)" | tee -a "$LOG"
        stopped=1
    fi
    if [ -n "$main_pid" ] && ! kill -0 "$main_pid" 2>/dev/null; then
        echo "MAIN_DIED pid=$main_pid t=$(date +%H:%M:%S.%N | cut -c1-12) i=$i" | tee -a "$LOG"
        exit 0
    fi
    sleep 0.05
done
echo "TIMEOUT main_alive=$([ -n "$main_pid" ] && kill -0 $main_pid 2>/dev/null && echo yes || echo no)" | tee -a "$LOG"
