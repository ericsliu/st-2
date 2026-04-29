#!/usr/bin/env python3
"""Decode a Uma packet-capture session into human-readable JSON.

Reads `data/packet_captures/session_*/index.jsonl`, pairs compress (request)
and decompress (response) round-trips by temporal adjacency, msgpack-decodes
the plaintext side of each (compress_in, decompress_out), and writes:

  - decoded.jsonl    — one JSON object per codec event, with decoded payload
  - pairs.jsonl      — one JSON object per request/response round-trip
  - summary.md       — brief overview: counts, size, endpoint key histogram

Ciphertext sides (compress_out, decompress_in) are recorded as opaque
base64 strings since they're AES+Base64-wrapped LZ4.

Usage:
    .venv/bin/python scripts/decode_uma_capture.py                  # latest session
    .venv/bin/python scripts/decode_uma_capture.py <session_dir>    # explicit
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uma_trainer.perception.carrotjuicer._decode import (
    CAPTURE_ROOT,
    decode_payload,
    is_plaintext_slot,
    latest_session as _latest_session,
    load_index,
)


def latest_session() -> Path:
    try:
        return _latest_session()
    except FileNotFoundError as e:
        raise SystemExit(str(e))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("session", nargs="?", help="session_* directory (defaults to latest)")
    ap.add_argument("--truncate-strings", type=int, default=0, help="truncate long string values to N chars in output (0=no truncation)")
    args = ap.parse_args()

    session_dir = Path(args.session).resolve() if args.session else latest_session()
    print(f"[*] decoding: {session_dir}")
    entries = load_index(session_dir)
    print(f"[*] {len(entries)} events in index")

    decoded_path = session_dir / "decoded.jsonl"
    pairs_path = session_dir / "pairs.jsonl"
    summary_path = session_dir / "summary.md"

    by_seq: dict[int, dict[str, dict]] = collections.defaultdict(dict)
    total_bytes = 0
    key_hist: collections.Counter = collections.Counter()
    endpoint_hist: collections.Counter = collections.Counter()

    with decoded_path.open("w") as fout:
        for e in entries:
            slot = e["slot"]
            direction = e["dir"]
            seq = e["seq"]
            plaintext = is_plaintext_slot(slot, direction)
            path = session_dir / e["file"]
            kind, payload = decode_payload(path, plaintext)
            total_bytes += e.get("len", 0)
            record = {
                "t": e.get("t"),
                "seq": seq,
                "slot": slot,
                "dir": direction,
                "len": e.get("len"),
                "kind": kind,
                "payload": payload,
            }
            fout.write(json.dumps(record, default=str) + "\n")
            by_seq[seq][direction] = record
            if plaintext and kind == "msgpack" and isinstance(payload, dict):
                for k in payload.keys():
                    key_hist[k] += 1
                if "data" in payload and isinstance(payload["data"], dict):
                    for k in payload["data"].keys():
                        endpoint_hist[f"data.{k}"] += 1

    # Pair: each seq has one in+out pair; but request<->response pairing
    # is across consecutive compress→decompress seqs. We group by adjacency.
    pairs: list[dict] = []
    cur_req: dict | None = None
    for seq in sorted(by_seq.keys()):
        evs = by_seq[seq]
        in_ev = evs.get("in")
        out_ev = evs.get("out")
        if not in_ev or not out_ev:
            continue
        slot = in_ev["slot"]
        if slot.startswith("compress"):
            cur_req = {
                "req_seq": seq,
                "t": in_ev["t"],
                "request": in_ev["payload"] if in_ev["kind"] == "msgpack" else None,
                "request_kind": in_ev["kind"],
                "request_len": in_ev["len"],
            }
        elif slot.startswith("decompress"):
            if cur_req is not None:
                pair = {
                    **cur_req,
                    "resp_seq": seq,
                    "resp_t": out_ev["t"],
                    "response": out_ev["payload"] if out_ev["kind"] == "msgpack" else None,
                    "response_kind": out_ev["kind"],
                    "response_len": out_ev["len"],
                    "rtt": round((out_ev["t"] or 0) - (cur_req["t"] or 0), 3),
                }
                pairs.append(pair)
                cur_req = None

    with pairs_path.open("w") as f:
        for p in pairs:
            f.write(json.dumps(p, default=str) + "\n")

    # Summary
    compress_events = sum(1 for e in entries if e["slot"].startswith("compress"))
    decompress_events = sum(1 for e in entries if e["slot"].startswith("decompress"))
    lines = [
        f"# Capture session summary",
        f"",
        f"- session: `{session_dir.name}`",
        f"- events: {len(entries)}  (compress={compress_events}, decompress={decompress_events})",
        f"- round-trip pairs: {len(pairs)}",
        f"- total bytes: {total_bytes:,}",
        f"- outputs:",
        f"  - `decoded.jsonl` — every event with msgpack-decoded plaintext",
        f"  - `pairs.jsonl` — request/response pairs (temporal adjacency)",
        f"",
        f"## Top request/response root-level keys",
        f"",
    ]
    for k, v in key_hist.most_common(20):
        lines.append(f"- `{k}`: {v}")
    lines.append("")
    lines.append("## Top `data.*` keys (endpoint-ish)")
    lines.append("")
    for k, v in endpoint_hist.most_common(30):
        lines.append(f"- `{k}`: {v}")

    summary_path.write_text("\n".join(lines) + "\n")

    print(f"[*] decoded.jsonl: {decoded_path}")
    print(f"[*] pairs.jsonl:   {pairs_path} ({len(pairs)} pairs)")
    print(f"[*] summary.md:    {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
