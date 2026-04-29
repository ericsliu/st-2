"""Tests for ``SessionTailer``: live tailer over a packet-capture dir.

Uses ``tests/fixtures/session_minihome/`` as a baseline session and copies
it into a tmp_path-rooted capture root so each test can mutate the layout
(append entries, rotate sessions) without touching real captures.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from uma_trainer.perception.carrotjuicer.session_tailer import SessionTailer

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "session_minihome"


def _copy_session(src: Path, dst: Path) -> Path:
    """Copy a fixture session into ``dst`` and return the new dir."""
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file():
            shutil.copyfile(f, dst / f.name)
    return dst


@pytest.fixture
def capture_root(tmp_path: Path) -> Path:
    root = tmp_path / "captures"
    _copy_session(FIXTURES, root / "session_20260101_000001")
    return root


def test_latest_response_returns_training_home(capture_root: Path):
    tailer = SessionTailer(root=capture_root, max_age_s=10_000.0)
    payload = tailer.latest_response()
    assert isinstance(payload, dict)
    data = payload.get("data")
    assert isinstance(data, dict)
    assert "chara_info" in data
    assert "home_info" in data


def test_endpoint_filter_skips_non_matching(capture_root: Path):
    tailer = SessionTailer(root=capture_root, max_age_s=10_000.0)
    # No response in fixture has this key, so we should get None.
    assert tailer.latest_response(endpoint_keys=("does_not_exist",)) is None


def test_picks_up_new_session_dir(capture_root: Path, tmp_path: Path):
    tailer = SessionTailer(root=capture_root, max_age_s=10_000.0)
    # Prime the tailer on the original session.
    p1 = tailer.latest_response_with_meta()
    assert p1 is not None
    _, _, bin_path1 = p1

    # Drop a newer session dir alongside the existing one.
    new_session = capture_root / "session_20260101_000002"
    _copy_session(FIXTURES, new_session)
    # Force its mtime later than the first one to be safe.
    later = time.time() + 10
    os.utime(new_session / "index.jsonl", (later, later))

    p2 = tailer.latest_response_with_meta()
    assert p2 is not None
    _, _, bin_path2 = p2
    # Path should now resolve under the newer session dir.
    assert bin_path2.parent.name == "session_20260101_000002"
    assert bin_path1.parent != bin_path2.parent


def test_appended_index_entry_is_picked_up(capture_root: Path):
    tailer = SessionTailer(root=capture_root, max_age_s=10_000.0)
    p1 = tailer.latest_response_with_meta()
    assert p1 is not None
    _, _, bin_path1 = p1

    # Append a synthetic newest index entry pointing at an earlier .bin
    # whose decoded msgpack is also a training-home response (seq 6 file).
    session = capture_root / "session_20260101_000001"
    new_entry = {
        "t": 999.0,
        "seq": 99,
        "slot": "decompress_late",
        "dir": "out",
        "len": (session / "000006_decompress_initial_out.bin").stat().st_size,
        "sent": 0,
        "truncated": False,
        "file": "000006_decompress_initial_out.bin",
    }
    with (session / "index.jsonl").open("a") as f:
        f.write(json.dumps(new_entry) + "\n")

    p2 = tailer.latest_response_with_meta()
    assert p2 is not None
    payload, _, _ = p2
    assert isinstance(payload, dict)
    assert "data" in payload  # Still a valid training-home shape.


def test_is_fresh_respects_max_age(capture_root: Path):
    tailer = SessionTailer(root=capture_root, max_age_s=10_000.0)
    assert tailer.is_fresh() is True

    stale = SessionTailer(root=capture_root, max_age_s=0.0)
    # max_age_s=0 ⇒ even a brand-new file is "stale".
    assert stale.is_fresh() is False


def test_returns_none_when_no_sessions(tmp_path: Path):
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    tailer = SessionTailer(root=empty_root)
    assert tailer.latest_response() is None
    assert tailer.is_fresh() is False
