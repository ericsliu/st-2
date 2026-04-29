"""Smoke checks for the compiled Frida agent bundle.

These do NOT run the agent — they verify the built JS contains the RPC
exports `scripts/frida_c1_probe.py` expects. Catches stale builds where
TypeScript changes in `frida_agent/src/*` weren't bundled into
`frida_agent/dist/agent.js` before running against Uma.
"""
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
AGENT_JS = REPO / "frida_agent" / "dist" / "agent.js"

# RPC exports the probe script + key probes rely on. camelCase in source,
# but bundled JS keeps the camelCase property names on rpc.exports.
REQUIRED_RPC = [
    "reportModules",
    "findLz4Candidates",
    "installLz4Hook",
    "probeStalkerOnNativeLz4",
    "probeStalkerHealth",
    "probeStalkerHealthEvents",
    "installPtraceBypass",
    "installDlopenWatcher",
    "discoverDeserializers",
    "enumerateLibNativeLz4",
    "installLibNativeLz4Hooks",
    "ping",
]


@pytest.fixture(scope="module")
def bundle_text() -> str:
    if not AGENT_JS.exists():
        pytest.skip(f"agent bundle missing at {AGENT_JS}; run `npm run build` in frida_agent/")
    return AGENT_JS.read_text()


@pytest.mark.parametrize("name", REQUIRED_RPC)
def test_rpc_export_present(bundle_text: str, name: str) -> None:
    assert name in bundle_text, f"rpc.exports.{name} missing from agent.js — rebuild required"


def test_stalker_health_emits_expected_message_types(bundle_text: str) -> None:
    for mtype in (
        "stalker_health_start",
        "stalker_health_followed",
        "stalker_health_done",
        "stalker_health_events_start",
        "stalker_health_events_followed",
        "stalker_health_events_done",
    ):
        assert mtype in bundle_text, f"{mtype!r} message type not in bundle — rebuild required"


def test_native_lz4_stalker_emits_diagnostic_fields(bundle_text: str) -> None:
    for field in ("anyBlockCompiles", "libnativeTotalCompiles", "libsSeen"):
        assert field in bundle_text, f"{field!r} diagnostic field not in bundle — rebuild required"


def test_libnative_lz4_hook_emits_expected_message_types(bundle_text: str) -> None:
    for mtype in (
        "libnative_lz4_phase",
        "libnative_lz4_method",
        "libnative_lz4_hook_resolved",
        "libnative_lz4_hit",
        "libnative_lz4_out",
        "libnative_lz4_stats",
    ):
        assert mtype in bundle_text, f"{mtype!r} message type not in bundle — rebuild required"
