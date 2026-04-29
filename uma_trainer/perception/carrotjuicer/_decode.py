"""Shared decoding helpers for the carrotjuicer capture pipeline.

Used by both the offline batch decoder (``scripts/decode_uma_capture.py``)
and the live ``SessionTailer`` that the bot uses to read the latest
captured response inside a turn.

A capture session lives at ``data/packet_captures/session_*/``. Each
HTTP round-trip writes four ``.bin`` files (compress in/out, decompress
in/out) plus one entry per write to ``index.jsonl``. Of those four
files only two are plaintext msgpack — see :func:`is_plaintext_slot`.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import msgpack

CAPTURE_ROOT = Path(__file__).resolve().parents[3] / "data" / "packet_captures"


def latest_session(root: Path | None = None) -> Path:
    """Return the most-recent ``session_*`` directory under ``root``."""
    base = root or CAPTURE_ROOT
    sessions = sorted(p for p in base.iterdir() if p.is_dir() and p.name.startswith("session_"))
    if not sessions:
        raise FileNotFoundError(f"no sessions found in {base}")
    return sessions[-1]


def load_index(session_dir: Path) -> list[dict]:
    """Read every entry from ``session_dir/index.jsonl``."""
    entries: list[dict] = []
    path = session_dir / "index.jsonl"
    if not path.exists():
        return entries
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries


def is_plaintext_slot(slot: str, direction: str) -> bool:
    """Plaintext msgpack lives on:

    - ``compress`` slot, ``in`` direction (request before LZ4+AES)
    - ``decompress`` slot, ``out`` direction (response after AES+LZ4)
    """
    if slot.startswith("compress"):
        return direction == "in"
    if slot.startswith("decompress"):
        return direction == "out"
    return False


def decode_payload(path: Path, is_plaintext: bool) -> tuple[str, object]:
    """Decode a single ``.bin`` blob.

    Returns ``(kind, payload)`` where ``kind`` is one of ``"msgpack"``,
    ``"ciphertext_b64"``, or ``"decode_err"``.
    """
    raw = path.read_bytes()
    if not is_plaintext:
        return "ciphertext_b64", base64.b64encode(raw).decode("ascii")
    try:
        obj = msgpack.unpackb(raw, raw=False, strict_map_key=False)
        return "msgpack", obj
    except Exception as e:
        return "decode_err", f"{type(e).__name__}: {e}"


__all__ = [
    "CAPTURE_ROOT",
    "decode_payload",
    "is_plaintext_slot",
    "latest_session",
    "load_index",
]
