#!/system/bin/sh
# Diagnostic: find the signature in ANY readable region (rx or rw),
# anon or file-backed, and report location + size.
#
# Args: $1 = pid
set -u

PID="${1:-}"
SIG='checkLoadPath_extractNativeLibs_true'
if [ -z "$PID" ]; then echo "usage: $0 <pid>"; exit 2; fi

MAPS=/proc/$PID/maps
MEM=/proc/$PID/mem
WORK=/data/local/tmp/ushield
mkdir -p "$WORK"
CHUNK=$WORK/diag_chunk.bin

# Look at ALL r-x regions regardless of file backing.
grep -E '^[0-9a-f]+-[0-9a-f]+ r..p' "$MAPS" > $WORK/diag_maps.txt
total=$(wc -l < $WORK/diag_maps.txt)
echo "scanning $total readable regions"

count=0
while read line; do
    range=$(echo "$line" | awk '{print $1}')
    prot=$(echo "$line" | awk '{print $2}')
    backing=$(echo "$line" | awk '{for (i=6;i<=NF;i++) printf "%s ", $i; print ""}' | sed 's/ *$//')

    start_hex=$(echo "$range" | cut -d- -f1)
    end_hex=$(echo "$range" | cut -d- -f2)
    start=$(printf '%d' 0x$start_hex 2>/dev/null)
    end=$(printf '%d' 0x$end_hex 2>/dev/null)
    [ -z "$start" ] && continue
    size=$((end - start))
    # Skip trivially small (<16k) and absurdly big (>64MB) regions.
    [ "$size" -lt 16384 ] && continue
    [ "$size" -gt 67108864 ] && continue

    blocks=$((size / 4096))
    skip_blocks=$((start / 4096))
    dd if="$MEM" of="$CHUNK" bs=4096 skip=$skip_blocks count=$blocks 2>/dev/null
    [ ! -s "$CHUNK" ] && continue
    hit=$(grep -aob "$SIG" "$CHUNK" | head -1 | cut -d: -f1)
    if [ -n "$hit" ]; then
        count=$((count+1))
        sig_addr=$((start + hit))
        printf 'HIT range=%s prot=%s size=0x%x backing=[%s] sig_addr=0x%x\n' \
            "$range" "$prot" "$size" "$backing" "$sig_addr"
    fi
done < $WORK/diag_maps.txt

rm -f "$CHUNK"
echo "total_hits=$count"
