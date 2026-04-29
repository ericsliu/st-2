#!/system/bin/sh
# Grab /proc/pid/maps snapshots of com.cygames.umamusume as soon as it
# starts, and run the signature diagnostic on the earliest-alive pid.
# All output goes under /data/local/tmp/ushield/.
set -u

PKG=com.cygames.umamusume
OUT=/data/local/tmp/ushield
mkdir -p "$OUT"
rm -f $OUT/maps_*.txt $OUT/diag_*.log

start=$(date +%s)
pid=""
while :; do
    elapsed=$(( $(date +%s) - start ))
    [ "$elapsed" -gt 30 ] && { echo "no pid in 30s"; exit 1; }
    pid=$(pidof $PKG 2>/dev/null | awk '{print $1}')
    [ -n "$pid" ] && break
    sleep 0.05
done
echo "pid=$pid at t+${elapsed}s"

# Snapshot maps immediately, then every 500ms up to 5 snapshots.
for i in 1 2 3 4 5; do
    cp /proc/$pid/maps $OUT/maps_$i.txt 2>/dev/null || break
    echo "snap $i: $(wc -l < $OUT/maps_$i.txt) lines"
    sleep 0.5
done

# Run diag against current state.
/data/local/tmp/device_shield_diag.sh $pid > $OUT/diag.log 2>&1
echo "diag complete:"
cat $OUT/diag.log
