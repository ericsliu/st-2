#!/system/bin/sh
# Root-side shield patcher for Uma Global (BlueStacks).
#
# Locates HyperTech CrackProof unpacked shield payload in an anonymous
# rx mapping inside com.cygames.umamusume, then patches the writer
# and reader functions via /proc/<pid>/mem.
#
# Args: $1 = pid
# Exits: 0 = patched, 1 = not found, 2 = arg error

set -u

PID="${1:-}"
SIG_STR='checkLoadPath_extractNativeLibs_true'
SIG_OFFSET=74245         # 0x12205
WRITER_OFFSET=71952      # 0x11910
READER_OFFSET=71916      # 0x118ec

if [ -z "$PID" ]; then
    echo "usage: $0 <pid>" >&2
    exit 2
fi

MAPS="/proc/$PID/maps"
MEM="/proc/$PID/mem"

if [ ! -r "$MAPS" ]; then
    echo "ERR cannot read $MAPS" >&2
    exit 2
fi

WORK=/data/local/tmp/ushield
mkdir -p "$WORK"
CHUNK="$WORK/chunk.bin"

# Grab all anon executable regions (r-xp AND rwxp — shield lives in
# an rwxp region on BlueStacks).  awk filters to anon only.
grep -E '^[0-9a-f]+-[0-9a-f]+ r[-w]xp' "$MAPS" \
    | awk '$6=="" {print $1}' \
    > "$WORK/anon_rx.txt"

FOUND=0
SHIELD_BASE=0
CANDIDATES=0
SCANNED=0

while read range; do
    CANDIDATES=$((CANDIDATES + 1))
    start_hex=$(echo "$range" | cut -d- -f1)
    end_hex=$(echo "$range" | cut -d- -f2)
    start=$(printf '%d' 0x$start_hex 2>/dev/null)
    end=$(printf '%d' 0x$end_hex 2>/dev/null)
    if [ -z "$start" ] || [ -z "$end" ]; then continue; fi
    size=$((end - start))
    # Filter by size window: shield payload is 0x17000 bytes (94208).
    if [ "$size" -lt 77824 ]; then continue; fi    # 0x13000
    if [ "$size" -gt 524288 ]; then continue; fi   # 0x80000

    blocks=$((size / 4096))
    skip_blocks=$((start / 4096))
    dd if="$MEM" of="$CHUNK" bs=4096 skip=$skip_blocks count=$blocks 2>/dev/null
    if [ ! -s "$CHUNK" ]; then continue; fi
    SCANNED=$((SCANNED + 1))

    hit=$(grep -aob "$SIG_STR" "$CHUNK" | head -1 | cut -d: -f1)
    if [ -n "$hit" ]; then
        SIG_ADDR=$((start + hit))
        SHIELD_BASE=$((SIG_ADDR - SIG_OFFSET))
        printf 'FOUND region=%s sig_addr=0x%x shield_base=0x%x\n' \
            "$range" "$SIG_ADDR" "$SHIELD_BASE"
        FOUND=1
        break
    fi
done < "$WORK/anon_rx.txt"

rm -f "$CHUNK"

if [ "$FOUND" = 0 ]; then
    echo "NOT_FOUND candidates=$CANDIDATES scanned=$SCANNED"
    exit 1
fi

WRITER_ADDR=$((SHIELD_BASE + WRITER_OFFSET))
READER_ADDR=$((SHIELD_BASE + READER_OFFSET))

# Writer patch: `ret` = c0 03 5f d6
printf '\xc0\x03\x5f\xd6' \
    | dd of="$MEM" bs=1 count=4 seek=$WRITER_ADDR conv=notrunc 2>/dev/null
# Reader patch: `mov w0,#0 ; ret` = 00 00 80 52 c0 03 5f d6
printf '\x00\x00\x80\x52\xc0\x03\x5f\xd6' \
    | dd of="$MEM" bs=1 count=8 seek=$READER_ADDR conv=notrunc 2>/dev/null

printf 'PATCHED writer=0x%x reader=0x%x\n' "$WRITER_ADDR" "$READER_ADDR"

V_WRITER=$(dd if="$MEM" bs=1 count=4 skip=$WRITER_ADDR 2>/dev/null \
    | od -An -vtx1 | tr -d ' \n')
V_READER=$(dd if="$MEM" bs=1 count=8 skip=$READER_ADDR 2>/dev/null \
    | od -An -vtx1 | tr -d ' \n')
echo "VERIFY writer=$V_WRITER reader=$V_READER"

if [ "$V_WRITER" = "c0035fd6" ] && [ "$V_READER" = "00008052c0035fd6" ]; then
    echo "OK patches confirmed in memory"
    exit 0
else
    echo "WARN verify mismatch"
    exit 1
fi
