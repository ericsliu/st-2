"""Live tailer for the active packet-capture session.

The Frida probe (``scripts/frida_c1_probe.py``) writes one timestamped
session directory under ``data/packet_captures/session_*/`` per attach.
For each HTTP round-trip it appends entries to ``index.jsonl`` and
writes the four codec ``.bin`` files. ``SessionTailer`` watches that
directory and exposes the most recently captured plaintext response so
``scripts/auto_turn.py`` can build ``GameState`` from packet data
instead of OCR.

Design points:

- The newest ``session_*`` directory is rediscovered on every call.
  Probe restarts (which create a new session dir) are followed
  automatically; we never pin to a specific session at construction.
- Tail by re-reading ``index.jsonl`` from a remembered byte offset; on
  inode change (new session) the offset resets. The file is small
  (one short line per codec event), so this is cheap.
- Decoded msgpack payloads are LRU-cached by absolute path. Paths
  never get re-used within a session and the per-call cost of an
  msgpack decode is ~1 ms, but turn-loop scans hit the same payload
  several times so the cache is still worth it.
- ``is_fresh()`` keys off the most recent ``index.jsonl`` write; a
  caller is responsible for deciding whether the latest response is
  recent enough to drive that turn's decision.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from ._decode import (
    CAPTURE_ROOT,
    decode_payload,
    is_plaintext_slot,
    load_index,
)


# Module-level cache so repeated decodes across multiple SessionTailer
# instances share work. Bin paths are unique per session.
@lru_cache(maxsize=128)
def _decode_cached(path_str: str) -> object | None:
    """Decode a plaintext .bin file to a msgpack dict, or return None."""
    path = Path(path_str)
    if not path.exists():
        return None
    kind, payload = decode_payload(path, is_plaintext=True)
    if kind != "msgpack":
        return None
    return payload


@dataclass
class _SessionState:
    session_dir: Path
    inode: int
    offset: int = 0           # byte offset into index.jsonl already consumed
    entries: list[dict] = field(default_factory=list)
    last_mtime: float = 0.0   # mtime of index.jsonl at last refresh


class SessionTailer:
    """Read-only watcher over the latest live capture session.

    Parameters
    ----------
    root:
        Capture root, default ``data/packet_captures/``.
    max_age_s:
        Maximum age (seconds) of the most recent index entry for
        :meth:`is_fresh` to return True. Default 30s.
    """

    def __init__(
        self,
        root: Path | None = None,
        *,
        max_age_s: float = 30.0,
    ) -> None:
        self.root = root or CAPTURE_ROOT
        self.max_age_s = max_age_s
        self._state: Optional[_SessionState] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def latest_response(
        self,
        *,
        endpoint_keys: tuple[str, ...] | None = None,
    ) -> dict | None:
        """Return the newest decoded response payload, or None.

        ``endpoint_keys``: optional filter — only return a response whose
        ``data.*`` contains at least one of the listed keys (e.g.
        ``("chara_info", "home_info")`` to require a training-home
        response).
        """
        meta = self.latest_response_with_meta(endpoint_keys=endpoint_keys)
        return meta[0] if meta is not None else None

    def latest_response_with_meta(
        self,
        *,
        endpoint_keys: tuple[str, ...] | None = None,
    ) -> tuple[dict, float, Path] | None:
        """Return ``(payload, age_s, bin_path)`` for the newest response.

        ``age_s`` is wall-clock seconds since the index entry's
        timestamp was written.
        """
        self._refresh()
        st = self._state
        if st is None:
            return None
        # Walk entries from newest backwards, looking for a plaintext
        # response (decompress out) whose decoded payload matches the
        # endpoint filter (if any).
        for entry in reversed(st.entries):
            slot = entry.get("slot", "")
            direction = entry.get("dir", "")
            if not is_plaintext_slot(slot, direction):
                continue
            if not slot.startswith("decompress"):
                continue
            bin_path = st.session_dir / entry["file"]
            payload = _decode_cached(str(bin_path))
            if not isinstance(payload, dict):
                continue
            if endpoint_keys is not None:
                data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                if not any(k in data for k in endpoint_keys):
                    continue
            age = max(0.0, st.last_mtime - entry.get("t", 0.0)) if False else self._wall_age(entry, st)
            return payload, age, bin_path
        return None

    def is_fresh(self) -> bool:
        """True iff the newest index entry was written within ``max_age_s``."""
        self._refresh()
        st = self._state
        if st is None or not st.entries:
            return False
        # Use the index file's mtime as a wall-clock proxy — the entry's
        # ``t`` field is monotonic-since-attach, not epoch.
        age = time.time() - st.last_mtime
        return age <= self.max_age_s

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _wall_age(self, entry: dict, st: _SessionState) -> float:
        """Approximate wall-clock age for ``entry`` using index mtime.

        ``entry["t"]`` is monotonic seconds since probe attach, not epoch
        time, so we can only compute relative ages. Treat the most recent
        entry as ``time.time() - last_mtime`` and earlier entries as
        offset by their relative ``t`` distance.
        """
        if not st.entries:
            return 0.0
        last_t = st.entries[-1].get("t", 0.0)
        rel = max(0.0, last_t - entry.get("t", 0.0))
        return (time.time() - st.last_mtime) + rel

    def _refresh(self) -> None:
        """Re-scan latest session dir + tail index.jsonl from last offset."""
        try:
            current = self._latest_session_dir()
        except FileNotFoundError:
            self._state = None
            return
        if current is None:
            self._state = None
            return

        index_path = current / "index.jsonl"
        try:
            stat = index_path.stat()
        except FileNotFoundError:
            self._state = None
            return

        st = self._state
        if st is None or st.session_dir != current or st.inode != stat.st_ino:
            # New session, or index file was rotated under us. Re-scan.
            st = _SessionState(session_dir=current, inode=stat.st_ino)
            try:
                st.entries = load_index(current)
            except FileNotFoundError:
                st.entries = []
            st.offset = stat.st_size
            st.last_mtime = stat.st_mtime
            self._state = st
            return

        # Same session — append any newly written lines.
        if stat.st_size > st.offset:
            with index_path.open() as f:
                f.seek(st.offset)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        st.entries.append(__import__("json").loads(line))
                    except Exception:
                        continue
            st.offset = stat.st_size

        st.last_mtime = stat.st_mtime

    def _latest_session_dir(self) -> Path | None:
        if not self.root.exists():
            return None
        sessions = sorted(
            (p for p in self.root.iterdir() if p.is_dir() and p.name.startswith("session_")),
            key=lambda p: p.name,
        )
        return sessions[-1] if sessions else None


__all__ = ["SessionTailer"]
