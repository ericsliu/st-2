# Packet Interception Spec — Addendum: Frida-Server Pivot

**Status**: Supersedes WS-1, WS-2, WS-4 in `PACKET_INTERCEPTION_SPEC.md`. WS-3, WS-5, WS-6, WS-7 unchanged.

**Date**: 2026-04-17

> **Update 2026-04-18:** WS-4's LZ4-hook-in-libnative.so approach is dead (shield detects Interceptor trampolines → SIGSEGV in 1–2 s). WS-1 and WS-2 still stand. See `PACKET_INTERCEPTION_SPEC_ADDENDUM_2.md` for the revised WS-4 (IL2CPP hook via `frida-il2cpp-bridge`).

---

## Context: Why We're Pivoting

The original spec recommended the Hachimi Edge plugin path specifically to **avoid root**. That constraint flipped when we confirmed Uma Musume Global has an anti-tamper check the Hachimi Edge path cannot survive on Android, and when the user enabled MuMu's root toggle to unblock progress.

### What we tried (and why it failed)

1. **Hachimi Edge v0.23.1 via UmaPatcher Edge** — built the proxy-patched APK (libmain_orig.so + Hachimi's libmain.so), installed on MuMu. Game crashed with SIGABRT (signal 6, code -1 SI_QUEUE) at ~130ms post-launch, before Hachimi's logger even initialized.

2. **Hachimi Edge v0.22.4** (downgrade) — same SIGABRT, same timing. Ruled out version-specific regression.

3. **BlueStacks Air instead of MuMu** — same SIGABRT. Ruled out emulator-specific cause.

4. **Frida Gadget (libfrida-gadget.so) injection via patchelf --add-needed** — same SIGABRT. Ruled out Hachimi-specific cause.

5. **Control test: re-signed original APK with zero library changes** — same SIGABRT at 132ms. **Isolated the root cause: any signature mismatch triggers an abort in `libnative.so` regardless of what was modified.**

6. **Research on Hachimi Edge + Global Android compatibility** — Official Hachimi Edge docs (EN/JP/CN) state plainly: *"Hachimi Edge does not support the Global Version on Android!"* No community-published signature-check bypass exists. The only functional Global Android path in Hachimi's ecosystem is the rooted **Zygisk module**, which requires Magisk's Zygisk framework.

### What MuMu's root toggle gives us

MuMu Player Pro offers a built-in root toggle. Enabling it:
- Makes `adb shell` run as `uid=0(root)` directly (no `su` prompt)
- Places `/system/bin/su` in the image
- Does **NOT** install Magisk (no `/data/adb/modules`, no `/sbin/magisk`)
- Does **NOT** provide Zygisk

So Hachimi's Zygisk path is still not directly available without installing Magisk on top. But we no longer need it: with root, we can run **frida-server** directly and attach to the unmodified game APK. No APK re-signing → no signature abort.

---

## Pivot: Frida-Server + Custom Agent

We abandon the Hachimi plugin architecture and replace it with a **frida-server + JavaScript agent** approach. This matches how CarrotJuicer itself works on rooted JP Android, and is a well-trodden path for Unity game instrumentation on Android.

### New architecture

```
┌─────────────────────────────────────────────────────┐
│  Uma Musume Process (UNMODIFIED original APK)       │
│                                                     │
│  libnative.so (game crypto/compression)             │
│    → LZ4_decompress_safe() ← HOOKED by Frida       │
│    → output buffer: raw msgpack                     │
│      → sent via send() to host                      │
│                                                     │
│  frida-server (root, /data/local/tmp/)              │
│    ↑ ptrace-attached to game process                │
│                                                     │
└────────────────────┬────────────────────────────────┘
                     │ Frida protocol over adb
                     ▼
┌─────────────────────────────────────────────────────┐
│  Bot Process (Python, on host Mac)                  │
│                                                     │
│  frida.get_device().attach(pid)                     │
│    → script.on('message', callback)                 │
│      → msgpack.unpackb()                            │
│        → PacketRouter → GameState                   │
│          → Decision Engine → ADB tap                │
└─────────────────────────────────────────────────────┘
```

### Advantages over the Hachimi plugin path

- **No APK modification** → no signature check trigger → no SIGABRT
- **JavaScript agent** (~50–100 LOC) vs. Rust plugin (~300–500 LOC)
- **Hot reload** — edit the agent, save, Frida re-injects with no rebuild
- **Built-in module enumeration, symbol resolution, memory scanning** in Frida's JS API
- **No cross-compile toolchain** — no Android NDK, no `cargo build --target aarch64-linux-android`
- **Host↔agent message passing built-in** — `send()`/`recv()` over the Frida protocol, replaces the Unix-socket + ADB-forward plumbing for Stage 1
- **No Hachimi dependency** — we are not at the mercy of Hachimi Edge's release cadence or their Global-Android support decisions

### New disadvantages

- **frida-server must be running before attach** — one extra step per bot launch (scripted via adb)
- **Root is visible to the game** — mitigated by MuMu's built-in hiding; Uma Musume Global is not known to check for root server-side. Local root checks in `libnative.so` (if any) haven't fired in our tests.
- **Frida is a known instrumentation framework** — some games detect `frida-server` by name, open ports, or inline hooks. Mitigation if needed: rename the binary, use `frida-inject` with a non-default config, or spawn frida-server on a random port.
- **No Hachimi overlay** — but we never needed it for the bot's packet-interception goal.

---

## Revised Work Streams

### WS-1 (REVISED): Root Emulator + frida-server Attached

**Owner**: Agent with shell access to Mac + MuMuPlayer
**Duration**: 0.5–1 day
**Blocked by**: Nothing (MuMu root toggle already enabled as of 2026-04-17)
**Blocks**: WS-4 (hook implementation needs a working attach target)

**Goal**: frida-server running as root inside MuMu, frida CLI on host Mac successfully attaching to `com.cygames.umamusume`, enumeration of the game's loaded modules confirming `libnative.so` is present.

**Context for the agent**:

MuMu Player Pro on macOS (Apple Silicon M1, Android 12, arm64-v8a) has root enabled via its settings toggle. `adb shell` runs as `uid=0(root)` directly. The game Uma Musume: Pretty Derby Global (`com.cygames.umamusume`) should already be installed from the **original unmodified** Qoopy/APKPure APK — if a Hachimi-patched version was previously installed, uninstall it first (`adb uninstall com.cygames.umamusume`) and reinstall the stock split APKs from `data/ws1_work/global-base.apk` and `data/ws1_work/global-split-arm64.apk` using `adb install-multiple`.

Do **not** re-sign or modify the APKs — the signature check in `libnative.so` will abort the process. We verified this in the previous session: any re-signed APK (even with zero library changes) SIGABRTs at ~130ms post-launch.

Frida version compatibility matters. The host-side `frida` Python package version must match or exceed the `frida-server` version; mismatched versions sometimes fail to attach. Pin both to the same release. The Frida releases page is at https://github.com/frida/frida/releases — pick the latest stable and grab `frida-server-<version>-android-arm64.xz` for the server and `pip install frida==<version>` for the host.

**Steps**:

1. Verify root is active:
   ```
   adb -s 127.0.0.1:5555 shell id
   # expected: uid=0(root) gid=0(root) ...
   ```

2. Verify the game is installed unmodified:
   ```
   adb -s 127.0.0.1:5555 shell pm path com.cygames.umamusume
   # note the APK paths — record them in data/ws1_setup_log.md
   ```
   If no output or the install is a Hachimi-patched build from the earlier attempt, uninstall and reinstall the stock APKs from `data/ws1_work/`.

3. Launch the game manually once to confirm it runs on the unmodified install (should reach title screen without SIGABRT). This confirms we've fully reverted from any earlier patched install.

4. Download `frida-server-<latest>-android-arm64.xz` from the Frida releases page. Decompress (`xz -d`) and push to the emulator:
   ```
   adb -s 127.0.0.1:5555 push frida-server /data/local/tmp/frida-server
   adb -s 127.0.0.1:5555 shell chmod 755 /data/local/tmp/frida-server
   ```

5. Run frida-server in the background as root (MuMu's adb shell is already root):
   ```
   adb -s 127.0.0.1:5555 shell "/data/local/tmp/frida-server &"
   ```

6. On the host Mac, install the matching Frida Python package in the project venv:
   ```
   .venv/bin/pip install frida==<matching-version> frida-tools==<matching-version>
   ```

7. Verify host↔server connection:
   ```
   .venv/bin/frida-ps -U
   # should list running Android processes
   ```

8. Attach to Uma Musume (game must be running):
   ```
   .venv/bin/frida-ps -U | grep umamusume
   .venv/bin/frida -U -n com.cygames.umamusume
   ```
   At the Frida REPL:
   ```js
   Process.enumerateModules().filter(m => m.name.includes('native'))
   Module.findBaseAddress('libnative.so')
   ```

**Deliverables**:
- `data/ws1_setup_log.md` updated with: APK install paths, frida versions used, output of `Process.enumerateModules()` for the three target libraries (`libnative.so`, `libmain.so`, `libil2cpp.so`).
- A shell script `scripts/frida_start.sh` that pushes the server binary if missing, starts it in the background, waits for the port to be open, and prints "ready".
- Confirmation that `libnative.so` is loaded and its base address is readable.

**Failure modes**:
- `frida-ps -U` hangs → frida-server not running or USB-mode confusion. MuMu exposes Android via adb-over-tcp, so Frida should use that automatically; if not, try `frida-ps -H 127.0.0.1:27042` after binding frida-server to that port explicitly.
- Attach succeeds but game crashes within seconds → runtime anti-debug check in `libnative.so`. Mitigation options in order of simplicity: (a) rename frida-server binary to something neutral, (b) spawn with `frida -U -f com.cygames.umamusume --realm=emulated` to attach before any protection kicks in, (c) hook `ptrace()`/`rt_tgsigqueueinfo()` to see which check fires.
- Attach succeeds, game stays running, but `libnative.so` is missing from the module list → game hasn't loaded it yet (happens before login screen). Stay attached and poll until it appears.

---

### WS-2 (REVISED): Frida Agent + Host Driver

**Owner**: Agent with JS/TS + Python experience
**Duration**: 1 day
**Blocked by**: Nothing (development can happen with placeholder stubs, doesn't need the game running)
**Blocks**: WS-4 (hook impl builds on the agent skeleton)

**Goal**: A Frida TypeScript/JS agent that loads cleanly into the game process, logs `"agent ready"`, enumerates loaded modules, and reports back to the host. A Python driver script that launches the agent and prints agent messages. The pair forms the scaffolding that WS-4 will fill in with the actual LZ4 hook.

**Context for the agent**:

Frida agents are JavaScript (or TypeScript compiled to JS) that runs inside the target process. The host connects via the Frida Python library and receives messages from the agent via `send()`/`recv()`. The agent has access to a rich API for module enumeration, symbol resolution, memory reading/writing, and inline hooking (`Interceptor.attach`). Documentation: https://frida.re/docs/javascript-api/

We structure the project as:
- `frida_agent/` — TypeScript source for the in-process agent
  - `src/agent.ts` — entry point, module enumeration, host message handler
  - `src/hook_lz4.ts` — LZ4 decompression hook (filled in by WS-4)
  - `src/framing.ts` — msgpack framing for messages to host (stub for now)
  - `package.json` with `@types/frida-gum` and `frida-compile` for bundling
  - Build output: `frida_agent/dist/agent.js` (single bundled file)
- `uma_trainer/perception/carrotjuicer/frida_driver.py` — host-side driver
  - Connects via `frida.get_usb_device()` or `get_device_manager().enumerate_devices()`
  - Spawns or attaches to `com.cygames.umamusume`
  - Loads `dist/agent.js`
  - Registers `on_message` callback that receives `{type, payload, data}` from agent
  - For now, just print messages; wire into `StateManager` in WS-5

**Steps**:

1. Scaffold the TypeScript project. Use `frida-compile` (https://github.com/frida/frida-compile) for the build step — it handles the bundling and gives us a single `.js` file Frida can load.

2. Write the minimal agent:
   ```typescript
   // src/agent.ts
   console.log('[agent] loaded');

   function reportModules() {
     const mods = Process.enumerateModules()
       .filter(m => /libnative|libmain|libil2cpp/.test(m.name))
       .map(m => ({ name: m.name, base: m.base.toString(), size: m.size }));
     send({ type: 'modules', modules: mods });
   }

   rpc.exports = {
     reportModules,
   };

   send({ type: 'ready' });
   ```

3. Write the host driver:
   ```python
   # uma_trainer/perception/carrotjuicer/frida_driver.py
   import frida, sys, json
   from pathlib import Path

   AGENT_PATH = Path(__file__).parent.parent.parent.parent / 'frida_agent/dist/agent.js'

   def on_message(message, data):
       if message['type'] == 'send':
           print('[host]', message['payload'])
       elif message['type'] == 'error':
           print('[agent-error]', message['stack'], file=sys.stderr)

   def main():
       device = frida.get_usb_device(timeout=5)
       session = device.attach('com.cygames.umamusume')
       script = session.create_script(AGENT_PATH.read_text())
       script.on('message', on_message)
       script.load()
       print('[host] agent loaded; waiting for messages')
       script.exports_sync.report_modules()
       sys.stdin.read()  # keep alive until Ctrl-C

   if __name__ == '__main__':
       main()
   ```

4. Build the agent: `cd frida_agent && npm install && npx frida-compile src/agent.ts -o dist/agent.js`

5. Run the driver: `.venv/bin/python uma_trainer/perception/carrotjuicer/frida_driver.py`

6. Verify: host should print `[host] {'type': 'ready'}` then a `modules` message listing libnative.so with its base address.

**Deliverables**:
- `frida_agent/` directory with the TypeScript source, `package.json`, build config
- `frida_agent/build.sh` — one-command build
- `uma_trainer/perception/carrotjuicer/frida_driver.py` — host driver
- `uma_trainer/perception/carrotjuicer/tests/test_frida_driver.py` — unit test that mocks the Frida session and verifies message routing
- README inside `frida_agent/` documenting the build chain

**Failure modes**:
- `frida-compile` output fails to load (syntax error, missing polyfill) — check that all TS features used are supported by Frida's V8. No Node APIs (`fs`, `net`, `process.env`) — only Frida's own globals.
- Host driver attach throws `Failed to attach: the connection is closed` — frida-server not running or version mismatch. Check `.venv/bin/frida-ps -U` first.

---

### WS-4 (REVISED): LZ4 Hook in Frida Agent

**Owner**: Agent with reverse engineering + Frida experience
**Duration**: 2–3 days
**Blocked by**: WS-1 (need live attach target), WS-2 (need agent scaffold)
**Blocks**: WS-5 integration

**Goal**: The agent hooks `LZ4_decompress_safe` (or equivalent) in `libnative.so`, captures each decompressed buffer, frames it as `{type: 'packet', ...}` with the buffer as binary payload, and sends it to the host. Host-side driver writes each payload to `/tmp/carrot_relay/<timestamp>.msgpack` for WS-5 to consume.

**Context for the agent**:

This stream replaces the Rust `dobby-rs` hook described in the original WS-4. Frida's `Interceptor.attach` and `Interceptor.replace` handle the inline hook; `Memory.readByteArray` extracts the decompressed buffer; `send(message, data)` ships the buffer to the host as a zero-copy `bytes` object in Python.

Start by trying to resolve the LZ4 symbol by name. Uma Musume's `libnative.so` may have stripped symbols, in which case fall back to a byte-pattern signature scan. CarrotJuicer (CNA-Bld) has published signatures for Windows libnative — the Android build likely compiled the same source, so the LZ4 function body should be structurally similar. The Hakuraku project and older CarrotJuicer-Android forks may have Android-specific signatures.

**Steps**:

1. From the `frida -U -n com.cygames.umamusume` REPL, enumerate exports in libnative.so:
   ```js
   const lib = Process.getModuleByName('libnative.so');
   lib.enumerateExports().filter(e => /LZ4|decompress/i.test(e.name))
   ```
   If symbols are present, you'll see `LZ4_decompress_safe`, `LZ4_decompress_safe_usingDict`, etc.

2. If symbols stripped, search for strings first (`lib.enumerateSymbols()` may still return some; otherwise `Memory.scan(lib.base, lib.size, 'LZ4', ...)` for string references). Cross-reference with CarrotJuicer's known LZ4 entry-point signature (bytes from the start of `LZ4_decompress_safe`).

3. Hook the resolved function:
   ```typescript
   const target = Module.findExportByName('libnative.so', 'LZ4_decompress_safe');
   Interceptor.attach(target, {
     onEnter(args) {
       this.dst = args[1];
       this.maxOut = args[3].toInt32();
     },
     onLeave(retval) {
       const result = retval.toInt32();
       if (result > 0) {
         const buf = this.dst.readByteArray(result);
         send({ type: 'packet', size: result }, buf);
       }
     }
   });
   ```

4. On the host side, update `frida_driver.py`'s `on_message` to write packets to disk:
   ```python
   def on_message(message, data):
       if message['type'] == 'send' and message['payload']['type'] == 'packet':
           ts = time.time_ns()
           path = OUT_DIR / f'{ts}.msgpack'
           path.write_bytes(data)
   ```

5. Exercise the game: launch, log in, enter a Career, train once. Expect ~5–20 `.msgpack` files to appear in `/tmp/carrot_relay/`.

6. Validate: pull one file and inspect with `msgpack-tools` or Hakuraku (https://hakuraku.sshz.org/#/carrotjuicer). Should be a readable msgpack map with keys like `data_headers`, `data`, etc.

7. Differentiate request vs. response: if both directions are captured, filter by direction. CarrotJuicer's convention is that response buffers start with specific marker bytes or have different size profiles — document what we see.

**Deliverables**:
- `frida_agent/src/hook_lz4.ts` with the hook implementation
- Host-side writing logic in `frida_driver.py`
- Sample captured packets (5+ from different game actions) committed to `data/captured_packets/` with a README explaining each
- A document listing: the symbol name found (or signature pattern used), the function's offset in libnative.so, which call direction (request/response) each hook fires for, any edge cases

**Failure modes**:
- No LZ4 symbols found AND no matching signature → escalate to il2cpp-level hook. Hachimi's il2cpp metadata isn't available to us directly, but Frida has `IL2CPP` plugins (e.g., `il2cpp-resolver`) we can use. Document the libnative.so analysis so the il2cpp fallback can build on it.
- Hook fires but buffer contents are not msgpack → we've hooked the wrong function, or it's a different compression layer. Check for nested LZ4 calls (sometimes games double-compress).
- Hook fires on startup then game crashes → runtime anti-hook check. Try `Interceptor.replace` instead of `attach`, or hook a later function in the decompression chain.

---

## Unchanged Streams

**WS-3 (Schema Research)**, **WS-5 (Python Receiver)**, **WS-6 (GameState API)**, **WS-7 (Integration)** — all still apply as written in `PACKET_INTERCEPTION_SPEC.md`. Minor adjustments when those streams execute:

- **WS-5**: Stage 1 "file polling" now reads from `/tmp/carrot_relay/` (host-local) instead of pulling via ADB from `/sdcard/Android/media/...`. Stage 2 "socket streaming" is replaced by Frida's built-in `send(msg, data)` channel — the host driver's `on_message` callback **is** the receiver. The `SocketReceiver` class becomes a `FridaReceiver` that wraps the Frida script's message stream.
- **WS-6**: No changes. The `CarrotJuicerProvider` still wraps the `StateManager`; the fact that the underlying transport is Frida rather than a Unix socket is hidden below the provider interface.
- **WS-7**: Game-update resilience is simpler — no re-patching. A game update may invalidate the LZ4 hook's offset or signature, but the unmodified APK just updates via Google Play / Cygames and our agent re-injects. We re-run WS-4's hook resolution if needed.

---

## Revised Critical Path

```
WS-1 (frida-server running)    ─┐
WS-2 (Frida agent scaffold)    ─┼── GATE 1 ── WS-4 (LZ4 hook)       ─┐
WS-3 (Schema research)         ─┘              WS-5 (Python receiver) ─┼── GATE 2 ── WS-7 (Integration)
                                               WS-6 (GameState API)    ─┘
```

**Elapsed time estimate**: ~1.5–2 weeks with parallel execution (down from ~2.5–3 weeks), primarily because WS-2 is now ~1 day of TS/Python instead of ~3 days of Rust cross-compile setup, and WS-1 is ~0.5 day instead of ~1–2 days.

**Ban risk posture**: Unknown server-side, low observed. We use original unmodified APKs, so APK-signature detection does not apply. Local detection surface is frida-server presence; mitigations are available if needed but not preemptive. No Hachimi/Carrotless-style bans have been reported on rooted JP Android in the 5 years since CarrotJuicer launched; Global's server-side stance is unknown but the product ships without Play Integrity enforcement today. We will monitor for account issues during development and adjust.
