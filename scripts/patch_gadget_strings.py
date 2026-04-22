#!/usr/bin/env python3
"""Binary-patch Frida gadget strings to strip externally-visible IOCs.

Targets identifiers the kernel exposes to any process on the device:
  - thread names (visible via /proc/<pid>/task/<tid>/comm, capped at 16 bytes)
  - socket names (/proc/net/unix abstract namespace, /proc/net/tcp)
  - GLib-quark error domains (visible in memory dumps only)

Only fixed-length replacements — keeps the .rodata offsets identical so any
code-relative loads against these addresses still resolve. Only strings that
appear as null-terminated (i.e. are themselves a C string, not a substring
of a longer string) are patched, which prevents accidentally mangling class
names like "GMainContext".

Leaves the 800+ internal ``Frida.<Class>.<method>`` strings alone: those are
only visible via full-memory scan, and replacing them could break any
reflective symbol resolution the runtime relies on.

Usage:
    .venv/bin/python scripts/patch_gadget_strings.py <in.so> <out.so>
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

# Pairs of (original, replacement) — MUST be the same length.
# Replacements avoid "frida"/"gum"/"gmain" substrings and stay syntactically
# plausible (so a casual inspection doesn't immediately flag them).
PATCHES: list[tuple[bytes, bytes]] = [
    # Thread names (comm is truncated to 16 bytes by the kernel)
    (b"gum-js-loop",        b"gpu-js-pool"),        # 11
    (b"gmain",              b"mmain"),              # 5
    (b"gdbus",              b"mdbus"),              # 5

    # Socket identifiers in abstract namespace
    (b"frida-gadget-tcp-%u", b"hidra-widget-tcp-%u"),  # 19
    (b"frida-gadget-unix",   b"hidra-widget-unix"),    # 17

    # GLib-quark / misc identifiers (visible only via memory inspection, but
    # cheap to patch)
    (b"frida-context",              b"hidra-context"),              # 13
    (b"frida-gadget",               b"hidra-widget"),               # 12
    (b"frida-generate-certificate", b"hidra-generate-certificate"), # 26
    (b"frida-error-quark",          b"hidra-error-quark"),          # 17
]


def patch(data: bytearray) -> list[tuple[bytes, int]]:
    """Apply every patch in-place. Returns [(original, count), ...]."""
    report: list[tuple[bytes, int]] = []
    for orig, repl in PATCHES:
        assert len(orig) == len(repl), (
            f"patch mismatch: len({orig!r})={len(orig)} != len({repl!r})={len(repl)}"
        )
        # Only replace null-terminated occurrences so we never stomp on a
        # substring of a longer class/method/path name.
        needle = orig + b"\x00"
        replacement = repl + b"\x00"
        count = 0
        offset = 0
        while True:
            idx = data.find(needle, offset)
            if idx < 0:
                break
            data[idx : idx + len(needle)] = replacement
            count += 1
            offset = idx + len(needle)
        report.append((orig, count))
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="original libgadget.so")
    ap.add_argument("output", type=Path, help="destination patched .so")
    args = ap.parse_args()

    data = bytearray(args.input.read_bytes())
    size_in = len(data)
    sha_in = hashlib.sha256(data).hexdigest()[:16]

    report = patch(data)

    args.output.write_bytes(bytes(data))
    sha_out = hashlib.sha256(data).hexdigest()[:16]

    print(f"[*] in:  {args.input} ({size_in:,} B, sha256={sha_in})")
    print(f"[*] out: {args.output} ({len(data):,} B, sha256={sha_out})")
    assert len(data) == size_in, "length drift — fixed-length invariant broken"
    print(f"[*] patched identifiers:")
    for orig, count in report:
        flag = "" if count else "  ← NOT FOUND"
        print(f"    {orig.decode():<30} x{count}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
