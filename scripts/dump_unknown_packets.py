"""Print top-level key inventory for packets the schema classified as UNKNOWN
or _GENERIC, so we can see what shape they actually are."""
from __future__ import annotations

import sys
from pathlib import Path

import msgpack

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uma_trainer.perception.carrotjuicer.schema import (
    PacketDirection,
    PacketKind,
    parse_packet,
)


def main() -> int:
    captures_root = Path(__file__).resolve().parents[1] / "data" / "packet_captures"
    sessions = sorted(p for p in captures_root.iterdir() if p.is_dir())

    for session in sessions:
        for bin_path in sorted(session.glob("*.bin")):
            name = bin_path.name
            if "_decompress_" in name and name.endswith("_out.bin"):
                direction = PacketDirection.RESPONSE
            elif "_compress_" in name and name.endswith("_in.bin"):
                direction = PacketDirection.REQUEST
            else:
                continue
            try:
                raw = msgpack.unpackb(bin_path.read_bytes(), raw=False, strict_map_key=False)
            except Exception:
                continue
            if not isinstance(raw, dict):
                continue
            pkt = parse_packet(raw, direction=direction)
            if pkt.kind in (PacketKind.UNKNOWN, PacketKind.REQUEST_GENERIC):
                inner = raw.get("data") if "data" in raw and isinstance(raw["data"], dict) else raw
                if not isinstance(inner, dict):
                    continue
                keys = sorted(inner.keys())
                print(f"[{pkt.kind.name}] {session.name}/{bin_path.name}")
                print(f"   keys: {keys}")
                print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
