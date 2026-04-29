"""Host-side Frida driver for the Uma Musume agent.

Connects to the MuMu emulator via Frida's USB transport (adb-backed),
attaches to com.cygames.umamusume, loads frida_agent/dist/agent.js,
and routes messages from the agent to a local callback.

WS-2 scaffold: prints agent messages and module enumeration.
WS-4 will extend on_message() to write captured packets to disk.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import frida

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_PATH = REPO_ROOT / "frida_agent" / "dist" / "agent.js"
TARGET_PACKAGE = "com.cygames.umamusume"


class FridaDriver:
    """Attach (or spawn) Uma Musume with the Frida agent loaded."""

    def __init__(
        self,
        target: str = TARGET_PACKAGE,
        agent_path: Path = AGENT_PATH,
        on_message: Optional[Callable[[dict, Optional[bytes]], None]] = None,
    ) -> None:
        self.target = target
        self.agent_path = agent_path
        self.on_message = on_message or self._default_on_message
        self.device: Optional[frida.core.Device] = None
        self.session: Optional[frida.core.Session] = None
        self.script: Optional[frida.core.Script] = None
        self.loaded_modules: dict[str, dict] = {}
        self._libnative_event = threading.Event()

    def _default_on_message(self, message: dict, data: Optional[bytes]) -> None:
        if message.get("type") == "send":
            payload = message.get("payload") or {}
            ptype = payload.get("type") if isinstance(payload, dict) else None
            if ptype == "module_loaded":
                name = payload.get("name", "?")
                self.loaded_modules[name] = payload
                print(f"[agent] module_loaded: {name}  handle={payload.get('handle')}")
                if name == "libnative.so":
                    self._libnative_event.set()
            else:
                print(f"[agent] {json.dumps(payload)}")
            if data:
                print(f"[agent] (+{len(data)} bytes of binary payload)")
        elif message.get("type") == "error":
            print(f"[agent-error] {message.get('stack', message)}", file=sys.stderr)
        else:
            print(f"[agent-?] {message}")

    def wait_for_libnative(self, timeout: float = 30.0, poll_interval: float = 1.0) -> bool:
        """Block until libnative.so is present.

        Uses two strategies concurrently:
          - the dlopen hook in the agent (fast path; sets _libnative_event on load)
          - polling Process.enumerateModules from the host every poll_interval

        Returns True if libnative.so is found within timeout, else False.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._libnative_event.is_set():
                return True
            try:
                modules = self.report_modules("libnative\\.so")
            except frida.InvalidOperationError:
                return False
            if any(m["name"] == "libnative.so" for m in modules):
                self._libnative_event.set()
                return True
            time.sleep(poll_interval)
        return False

    def attach(self, spawn: bool = False, pid: Optional[int] = None) -> None:
        self.device = frida.get_usb_device(timeout=5)
        if spawn:
            new_pid = self.device.spawn([self.target])
            self.session = self.device.attach(new_pid)
            self._load_script()
            self.device.resume(new_pid)
        elif pid is not None:
            self.session = self.device.attach(pid)
            self._load_script()
        else:
            # Resolve by name; pick the top-level (lowest-pid) match if ambiguous.
            assert self.device is not None
            matches = [p for p in self.device.enumerate_processes() if p.name == self.target]
            if not matches:
                raise frida.ProcessNotFoundError(f"no process named {self.target!r}")
            matches.sort(key=lambda p: p.pid)
            target_pid = matches[0].pid
            self.session = self.device.attach(target_pid)
            self._load_script()

    def _load_script(self) -> None:
        assert self.session is not None
        if not self.agent_path.exists():
            raise FileNotFoundError(
                f"Agent bundle missing: {self.agent_path}. "
                f"Run frida_agent/build.sh first."
            )
        code = self.agent_path.read_text()
        self.script = self.session.create_script(code)
        self.script.on("message", self.on_message)
        self.script.load()

    def report_modules(self, pattern: Optional[str] = None) -> Any:
        assert self.script is not None
        return self.script.exports_sync.report_modules(pattern)

    def find_lz4_candidates(self) -> Any:
        assert self.script is not None
        return self.script.exports_sync.find_lz4_candidates()

    def ping(self) -> str:
        assert self.script is not None
        return self.script.exports_sync.ping()

    def detach(self) -> None:
        if self.session is not None:
            try:
                self.session.detach()
            except frida.InvalidOperationError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spawn",
        action="store_true",
        help="Spawn the target instead of attaching to a running instance.",
    )
    parser.add_argument(
        "--modules-pattern",
        default="libnative|libmain|libil2cpp",
        help="Regex for filtering module names in the initial dump.",
    )
    parser.add_argument(
        "--keep-alive",
        action="store_true",
        help="Keep the attach session alive until Ctrl-C (for later hook work).",
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=30.0,
        help="How long to wait after spawn for libnative.so to load.",
    )
    parser.add_argument(
        "--pid",
        type=int,
        default=None,
        help="Attach to a specific PID instead of resolving by name.",
    )
    args = parser.parse_args()

    drv = FridaDriver()
    drv.attach(spawn=args.spawn, pid=args.pid)
    assert drv.script is not None

    time.sleep(0.2)

    print(f"[driver] ping: {drv.ping()}")

    if args.spawn:
        print(f"[driver] waiting up to {args.wait_seconds}s for libnative.so to load...")
        if drv.wait_for_libnative(timeout=args.wait_seconds):
            print("[driver] libnative.so observed loading")
        else:
            print("[driver] libnative.so not observed; continuing anyway")

    time.sleep(0.3)
    modules = drv.report_modules(args.modules_pattern)
    print(f"[driver] modules matching /{args.modules_pattern}/i: {len(modules)}")
    for m in modules:
        print(f"  - {m['name']:<20} base={m['base']:>14} size={m['size']:>10}")

    lz4 = drv.find_lz4_candidates()
    print(f"[driver] LZ4 candidate exports in libnative.so: {len(lz4)}")
    for c in lz4:
        print(f"  - {c['name']:<40} @ {c['address']}")

    if args.keep_alive:
        print("[driver] keep-alive on; Ctrl-C to exit")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    drv.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
