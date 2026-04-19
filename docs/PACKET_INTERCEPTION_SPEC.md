# Packet Interception: Riru-CarrotJuicer vs. Hachimi Edge

> **2026-04-18 reading order:** this spec is the original design. The Hachimi Edge plugin path (recommendation below) is superseded by `PACKET_INTERCEPTION_SPEC_ADDENDUM.md` (Frida-server pivot), which is itself partially superseded by `PACKET_INTERCEPTION_SPEC_ADDENDUM_2.md` (IL2CPP hook pivot after the libnative LZ4 hook was found to trip the shield). Read all three in order.

## Executive Summary

We have two viable paths for intercepting decrypted game packets from Uma Musume: Pretty Derby on Android. Both achieve the same end result — structured msgpack game state data delivered to our bot process — but through fundamentally different injection mechanisms with different tradeoff profiles.

**Recommendation: Hachimi Edge plugin path.** It eliminates root entirely, has a simpler setup chain, is actively maintained, and has a larger community around the Global version. The cost is that we need to write a custom Rust/C plugin (~300-500 lines) and re-patch the APK on game updates.

---

## Comparison

### Riru-CarrotJuicer

**Injection method**: Magisk → Zygisk → Zygote process fork injection → PLT/GOT hook on `libnative.so`

**Requires root**: Yes. The device must be rooted with Magisk. Since Uma Musume refuses to boot on rooted devices, Shamiko or Zygisk Assistant must be installed to hide root from the game. This creates a dependency chain of: MuMuPlayer root toggle → Kitsune Magisk → Zygisk → Shamiko (root hiding) → HMA (app list hiding) → PlayIntegrityFix → CarrotJuicer module. Six components, any of which can break compatibility with each other or with the emulator.

**Maintenance status**: Last Android release was v1.0.3 in April 2021. Five years without an update. The Windows version (v1.7.1, May 2024) is more recent but still two years old. The codebase targets the Riru framework, which Magisk deprecated in favor of Zygisk in v24. Running it on current Magisk requires either a compatibility shim or a port.

**Global version compatibility**: Untested. Built for the JP client's `libnative.so`. The Global client uses the same protocol, but the binary offsets and symbol layout may differ. Needs verification.

**Detection surface**:
- Root is present on the device (hidden by Shamiko, but detectable in theory via mount namespace mismatches, PID inconsistencies, etc.)
- In-process hook modifies GOT entries (detectable via self-integrity checking)
- Zygisk module `.so` loaded into game process memory (detectable via `/proc/self/maps` scanning, mitigated by Shamiko)
- Notifier HTTP POST from game process to localhost (detectable via `/proc/self/net/tcp`)

**Advantages**:
- Proven approach — same technique CarrotJuicer has used for 5 years on JP
- Notifier mode provides real-time HTTP delivery of packets (no polling needed)
- Hook is transparent to the game's own code path (wrapper calls original, copies output)

**Disadvantages**:
- Requires root + a 6-component root-hiding stack
- Unmaintained on Android (5 years since last release)
- Riru framework deprecated
- Root hiding is a cat-and-mouse game — any Cygames root detection improvement could break it
- Complex setup with many failure modes
- Game update could break Shamiko compatibility independently of CarrotJuicer

---

### Hachimi Edge Plugin

**Injection method**: APK patching — `libmain.so` in the APK is replaced with a proxy that loads Hachimi, which loads plugins. All code runs as part of the game's own process from the perspective of the Android OS. No root, no Magisk, no Zygisk.

**Requires root**: No. UmaPatcher Edge patches the game APK at install time, re-signs it with a local key, and installs the modified package. The game boots normally on an unrooted device. Hachimi's code is loaded as if it were part of the game itself.

**Maintenance status**: Actively maintained. Last release v0.23.1 in February 2026 (2 months ago). Regular commits from multiple contributors. Active Discord community. Written in Rust with cross-platform support (Windows + Android).

**Global version compatibility**: The Hachimi Edge docs say "does not support Global Version on Android." Investigation reveals this is a **packaging/paths issue, not a hooking incompatibility**.

The root cause: JP and Global use different Android package names:
- **JP**: `jp.co.cygames.umamusume` (Google Play: `play.google.com/store/apps/details?id=jp.co.cygames.umamusume`)
- **Global**: `com.cygames.umamusume` (Google Play: `play.google.com/store/apps/details?id=com.cygames.umamusume`)

This means everything that references the package name differs between the two versions:
- Android data directory: `/data/data/com.cygames.umamusume/` instead of `/data/data/jp.co.cygames.umamusume/`
- Hachimi config path: `/sdcard/Android/media/com.cygames.umamusume/hachimi/` instead of the JP path
- UmaPatcher's app selection dropdown may not list the Global package
- CarrotJuicer file output paths reference the JP package name
- The `AndroidManifest.xml` application ID differs

The Hachimi Edge troubleshooting page confirms this obliquely: "This is likely to occur on global due to some file name differences not yet accounted for."

**Key evidence that the hooks themselves are compatible**:
- Global Windows (Steam) IS supported by Hachimi Edge — same Unity runtime, same il2cpp, same `libnative.dll`
- Global Android uses the same Unity engine and il2cpp compilation as JP Android
- The `libmain.so` proxy injection is package-name-agnostic — it works at the native library level
- The difference between "supported" and "not supported" is literally the platform (Windows vs Android), not the region (JP vs Global)

**What needs to happen for Global Android to work**:
1. UmaPatcher Edge needs to accept the Global APK (`com.cygames.umamusume`) as a patching target — this may work already if you manually select the APK file rather than choosing from installed apps
2. Hachimi's config/data directory paths need to resolve to the Global package name — Hachimi likely derives this from the running app's package context, so this may be automatic
3. Any hardcoded references to `jp.co.cygames.umamusume` in Hachimi's Rust source would need updating — a grep of the codebase will reveal these
4. Our CarrotRelay plugin's config path needs to use the correct package name

This is a WS-1 (Environment Setup) verification task, not a fundamental blocker.

**Detection surface**:
- No root on the device at all — root detection is a non-issue
- APK signature differs from the Google Play version (detectable if Cygames checks signing certificates server-side, but they haven't in 5 years of Hachimi/Carrotless/UmaTL usage on JP)
- Modified `libmain.so` in the APK (detectable via APK hash checking, but not currently done)
- Plugin `.so` loaded in process memory (same as any game library, hard to distinguish)

**Advantages**:
- No root required — eliminates the entire Magisk/Shamiko/HMA dependency chain
- Actively maintained, community-supported
- Plugin API is well-defined with il2cpp hooking, GUI integration, and DEX loading
- Hachimi already solves the hard problems (il2cpp metadata resolution, hook stability across game updates, cross-platform support)
- Game updates are handled by re-patching with UmaPatcher (semi-automated)
- Hachimi's own hooks survive game updates because they target il2cpp method signatures, not raw binary offsets

**Disadvantages**:
- We need to write a custom plugin for packet interception (doesn't exist yet)
- APK must be re-patched on every game update (UmaPatcher automates this but it's still a manual trigger)
- Google Play account login disabled (must use Data Link / Cygames ID)
- Google Play Store purchases disabled (must use Cygames store)
- Android 16 (September 2026) may restrict APK sideloading, potentially breaking the installation path
- Plugin must be compiled as a native ARM64 `.so` (Rust or C/C++)

---

## Decision Matrix

| Factor | Riru-CarrotJuicer | Hachimi Edge Plugin |
|--------|-------------------|---------------------|
| Requires root | Yes + hiding stack | No |
| Setup complexity | Very high (6 components) | Low (UmaPatcher + plugin) |
| Maintenance status | Abandoned (2021) | Active (2026) |
| Root detection risk | High (hidden but present) | None |
| APK signature risk | None (stock APK) | Present (re-signed APK) |
| Packet interception | Built-in (notifier mode) | Must write plugin |
| Game update resilience | Hook may break on libnative changes | Hachimi team updates for game changes |
| Community support | Minimal (JP-focused, old) | Active Discord, multiple contributors |
| Android 16 impact | None (root path unaffected) | May break sideloading |
| Language to write in | N/A (prebuilt) | Rust or C |

---

## Hachimi Edge Path: Technical Specification

### Overview

We will write a Hachimi Edge plugin ("CarrotRelay") that intercepts decrypted server response packets inside the game process and forwards them to our bot over a local socket. The plugin is a native ARM64 `.so` that hooks the same decompression path CarrotJuicer targets, but from within the Hachimi plugin framework rather than via Zygisk injection.

### Plugin Architecture

```
┌─────────────────────────────────────────────────────┐
│  Uma Musume Process (patched APK)                   │
│                                                     │
│  libmain.so (Hachimi proxy)                         │
│    → loads hachimi.so (Hachimi Edge core)            │
│      → loads carrot_relay.so (our plugin)            │
│                                                     │
│  libnative.so (game crypto/compression)             │
│    → LZ4_decompress_safe() ← HOOKED by plugin      │
│    → output buffer: raw msgpack                     │
│      → copied to Unix domain socket / HTTP POST     │
│                                                     │
└────────────────────┬────────────────────────────────┘
                     │ msgpack bytes
                     ▼
┌─────────────────────────────────────────────────────┐
│  Bot Process (Python, on host Mac via ADB forward)  │
│                                                     │
│  Socket listener → msgpack.unpack() → GameState     │
│    → Decision Engine → ADB tap                      │
└─────────────────────────────────────────────────────┘
```

### Hook Strategy: Two Options

#### Option A: Native libnative.so Hook (CarrotJuicer approach)

Hook the LZ4 decompression function directly in `libnative.so`. This is what CarrotJuicer does. Hachimi's `Interceptor` (backed by `dobby-rs`) can perform this hook — it's not limited to il2cpp methods.

```rust
// Pseudocode for the plugin's hook setup
use dobby_rs::hook;

// Find LZ4_decompress_safe in libnative.so
let libnative = dlopen("libnative.so");
let target = dlsym(libnative, "LZ4_decompress_safe");

// Hook it
static mut ORIGINAL: *const () = std::ptr::null();
unsafe {
    hook(target as *mut _, hooked_decompress as *mut _, &mut ORIGINAL as *mut _ as *mut _);
}

unsafe extern "C" fn hooked_decompress(
    src: *const u8, dst: *mut u8, compressed_size: i32, max_decompressed_size: i32
) -> i32 {
    // Call original
    let original: extern "C" fn(*const u8, *mut u8, i32, i32) -> i32 =
        std::mem::transmute(ORIGINAL);
    let result = original(src, dst, compressed_size, max_decompressed_size);

    if result > 0 {
        // Copy decompressed buffer and send to bot
        let data = std::slice::from_raw_parts(dst, result as usize);
        send_to_bot(data);
    }

    result
}
```

Pros: Proven interception point, identical data to CarrotJuicer, known to produce valid msgpack.
Cons: Depends on `LZ4_decompress_safe` symbol being exported. If symbols are stripped, falls back to signature scanning.

#### Option B: il2cpp-Level Hook (Higher-level)

Hook the C# method that receives the deserialized server response in the Unity game logic layer. Hachimi already resolves il2cpp metadata, so we can hook by class name + method name rather than binary offset.

```rust
// Pseudocode — hook the il2cpp method that processes server responses
let method = il2cpp::find_method(
    "Cygames.Umamusume.Network",  // namespace (needs reverse engineering)
    "ResponseHandler",             // class (needs reverse engineering)
    "OnResponse",                  // method (needs reverse engineering)
);
interceptor.hook(method, on_response_hook);
```

Pros: More resilient to `libnative.so` binary changes. il2cpp method signatures are more stable than native function offsets. Hachimi already maintains the il2cpp metadata.
Cons: Requires reverse-engineering the il2cpp class/method names for the response handler. The data at this level may already be partially deserialized (C# objects rather than raw msgpack), which changes the parsing strategy.

**Recommendation**: Start with Option A (native `libnative.so` hook). It's a known-good interception point, the data format is documented, and the Hakuraku/UmaLauncher ecosystem already has parsers for it. Fall back to Option B only if Cygames strips the LZ4 symbol or restructures `libnative.so`.

### Data Transport

The plugin runs inside the game process on the Android emulator. The bot runs as a Python process on the host Mac. We need to bridge the data out.

**Primary: Unix domain socket inside the emulator + ADB forward**

The plugin writes msgpack packets to a Unix domain socket at a well-known path (e.g., `/data/local/tmp/carrot_relay.sock`). On the host Mac:

```bash
adb forward localabstract:carrot_relay tcp:4693
```

The bot connects to `localhost:4693` on the host and receives a stream of msgpack packets. Each packet is length-prefixed (4-byte big-endian length header, then that many bytes of msgpack payload) so the bot can frame them correctly on the TCP stream.

**Fallback: File-based polling**

If socket forwarding proves unreliable, the plugin writes timestamped `.msgpack` files to the game's accessible storage directory (`/sdcard/Android/media/jp.co.cygames.umamusume/carrot_relay/`). The bot polls this directory over ADB:

```bash
adb shell ls /sdcard/Android/media/jp.co.cygames.umamusume/carrot_relay/
adb pull /sdcard/Android/media/.../carrot_relay/latest.msgpack
```

This adds ~100-200ms latency per packet but is simpler to implement and debug.

### Plugin Build Chain

The plugin is a Rust crate compiled to an ARM64 `.so`:

```
carrot_relay/
├── Cargo.toml
├── src/
│   ├── lib.rs          # Plugin entry point, Hachimi API registration
│   ├── hook.rs         # libnative.so hook setup via dobby-rs
│   ├── transport.rs    # Unix socket / file output
│   └── framing.rs      # Length-prefixed msgpack framing
└── build.rs            # ARM64 cross-compilation config
```

**Build requirements**:
- Rust toolchain with `aarch64-linux-android` target
- Android NDK (for libc and linker)
- `dobby-rs` crate (or direct FFI to Dobby C API)

```bash
# Cross-compile for Android ARM64
cargo build --target aarch64-linux-android --release
# Output: target/aarch64-linux-android/release/libcarrot_relay.so
```

The resulting `.so` is added to UmaPatcher Edge's plugin list before patching the game APK.

### Bot-Side Receiver (Python)

```python
import socket
import struct
import msgpack

def listen_for_packets(port=4693):
    """Connect to ADB-forwarded socket and yield parsed msgpack packets."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", port))

    buf = b""
    while True:
        # Read length header (4 bytes, big-endian)
        while len(buf) < 4:
            buf += sock.recv(4096)
        length = struct.unpack(">I", buf[:4])[0]
        buf = buf[4:]

        # Read payload
        while len(buf) < length:
            buf += sock.recv(4096)
        payload = buf[:length]
        buf = buf[length:]

        # Parse msgpack
        packet = msgpack.unpackb(payload, raw=False)
        yield packet
```

### Installation Flow

```
1. Download Global APK (from APKPure, Qoopy, etc.)
2. Download UmaPatcher Edge + carrot_relay.so
3. In UmaPatcher Edge:
   a. Select "Normal install"
   b. Add carrot_relay.so as a plugin
   c. Select the game APK
   d. Tap "Patch" → installs patched game
4. First launch: Hachimi Edge setup dialog appears
   a. Skip translation setup (Global is already English)
   b. Disable translation features in Config Editor
5. Configure carrot_relay via config file:
   /sdcard/Android/media/<PACKAGE_NAME>/hachimi/plugins/carrot_relay.json
   (where PACKAGE_NAME is com.cygames.umamusume for Global, jp.co.cygames.umamusume for JP)
   {
     "transport": "socket",
     "socket_path": "carrot_relay",
     "save_files": false
   }
6. On host Mac:
   adb forward localabstract:carrot_relay tcp:4693
7. Start bot listener, launch game
```

### Game Update Process

When Cygames pushes a game update:
1. Download the new APK
2. Open UmaPatcher Edge (plugins are preserved)
3. Patch the new APK (UmaPatcher applies Hachimi Edge + plugins automatically)
4. Install over the existing patched game (no uninstall needed)

Hachimi Edge typically updates within hours of a game update if hooks break. Since our plugin hooks `libnative.so` (not il2cpp), Hachimi updates are independent of our plugin — the plugin only needs updating if the LZ4 symbol changes or `libnative.so` restructures.

### Msgpack Schema Discovery

The msgpack packet schema is undocumented. To reverse-engineer it:

1. **Capture packets during manual play**: Run the game with the plugin active, play through a Career run manually, capture all packets to files.
2. **Use Hakuraku**: Upload `.msgpack` files to https://hakuraku.sshz.org/#/carrotjuicer for visual inspection of packet structure.
3. **Cross-reference with UmaLauncher source**: UmaLauncher (Python, GPL) parses these same packets for its training display. Its source code documents the field names and types for training state, events, skills, and races.
4. **Cross-reference with master.mdb**: The game's local SQLite database contains all static definitions (character IDs, skill IDs, race IDs, event IDs). Packet fields reference these IDs. The database is at `/data/data/com.cygames.umamusume/master/master.mdb` for Global (or `/data/data/jp.co.cygames.umamusume/master/master.mdb` for JP) and can be pulled via ADB.

### Risk Assessment (Hachimi Edge Path)

**What we lose vs. stock game**:
- Google Play auto-updates (must re-patch manually on each update)
- Google Play account login (must use Data Link or Cygames ID)
- Google Play purchases (must use Cygames store, which still works)

**What could break**:
- **Game update changes Unity version**: Hachimi Edge team handles this (they've survived multiple Unity updates). Our plugin is independent of il2cpp hooks.
- **Game update changes libnative.so**: Our plugin's hook target may shift. Mitigation: symbol-based lookup (resilient to recompilation), with fallback to signature scanning.
- **Android 16 restricts sideloading (September 2026)**: The APK patching install path may stop working. Mitigation: MuMuPlayer controls the Android version — we can stay on Android 14/15 indefinitely in the emulator, regardless of what real devices run.
- **Cygames checks APK signature server-side**: They could compare the installed APK's signing certificate against their expected one. This would break all Hachimi/Carrotless users (JP + Global). Given 5+ years of not doing this, the risk is low. Mitigation: none (fundamental to the APK patching approach).
- **UmaPatcher/Hachimi Edge project discontinued**: The project already survived one maintainer handoff (original Hachimi → Hachimi Edge). The codebase is open source (Rust, MIT-licensed). Worst case, we fork and maintain the minimum needed for our plugin to load.

### Work Breakdown: Parallelizable Tasks for Multi-Agent Execution

The work decomposes into **7 work streams** across **3 phases**. Streams within the same phase can execute in parallel. Phase boundaries are hard gates — all tasks in a phase must complete before the next phase starts.

```
PHASE 1: Foundation (parallel)          PHASE 2: Core (parallel)         PHASE 3: Integration
┌──────────────┐                        ┌──────────────┐                 ┌──────────────┐
│ WS-1: Env    │                        │ WS-4: Hook   │                 │ WS-7: Glue   │
│  Setup       │──┐                     │  Impl        │──┐              │  + Fallback   │
└──────────────┘  │                     └──────────────┘  │              └──────────────┘
┌──────────────┐  ├─── GATE 1 ───────── ┌──────────────┐  ├── GATE 2 ──►
│ WS-2: Plugin │  │                     │ WS-5: Python │  │
│  Scaffold    │──┤                     │  Receiver    │──┤
└──────────────┘  │                     └──────────────┘  │
┌──────────────┐  │                     ┌──────────────┐  │
│ WS-3: Schema │──┘                     │ WS-6: State  │──┘
│  Research    │                        │  Assembly    │
└──────────────┘                        └──────────────┘
```

---

## PHASE 1: Foundation

All three streams can run in complete isolation. No shared code dependencies. Each produces an artifact the next phase consumes.

---

### WS-1: Environment Setup

**Owner**: Agent with shell access to the Mac + MuMuPlayer
**Duration**: 1-2 days
**Blocking**: Nothing (can start immediately)
**Blocked by**: Nothing

**Goal**: A patched Uma Musume Global APK running inside MuMuPlayer macOS with Hachimi Edge active, confirming the hooking infrastructure loads on the Global client.

**Context for the agent**:

You are setting up an Android emulator environment on macOS (Apple Silicon M1). The game is Uma Musume: Pretty Derby (Global/English version). We need to install a modified version of the game that includes Hachimi Edge, a Rust-based game enhancement framework that hooks into the Unity il2cpp runtime.

Hachimi Edge is primarily documented for the JP version. The Global Android install page says "not supported" but our investigation shows this is a **packaging/paths issue, not a hooking incompatibility**. The critical difference:

- **JP Android package**: `jp.co.cygames.umamusume`
- **Global Android package**: `com.cygames.umamusume`

These are different Android application IDs, which means different data directories, different config paths, and potentially different UmaPatcher behavior. However, both versions use the same Unity runtime and il2cpp compilation. The `libmain.so` proxy injection is package-name-agnostic. Global Windows (Steam) is already supported by Hachimi Edge, confirming the game's internals are compatible.

Your primary job is to verify that UmaPatcher Edge can patch the Global APK and that Hachimi Edge's hooks initialize correctly despite the different package name.

**Steps**:
1. Install MuMuPlayer macOS (Apple Silicon native) from mumuplayer.com
2. Configure: 4+ CPU cores, 4+ GB RAM, Android 12+ instance
3. Download the Global APK for Uma Musume: Pretty Derby — package name is `com.cygames.umamusume` (from Qoopy or APKPure — NOT Google Play, since we need the raw APK files). Verify the package name matches by inspecting the APK manifest.
4. Download and install UmaPatcher Edge (https://github.com/kairusds/UmaPatcher-Edge/releases/latest) inside the emulator
5. In UmaPatcher Edge: select "Normal install", select the Global game APK. **Note**: UmaPatcher may not show the Global version in its app selector dropdown since it expects `jp.co.cygames.umamusume`. You may need to use the file picker to select the APK directly rather than choosing from installed apps.
6. If UmaPatcher refuses to patch or errors during patching, document the exact error. Try the manual patching process described in the Hachimi docs (extract APK, rename `libmain.so` to `libmain_orig.so`, insert Hachimi proxy, re-sign, install).
7. Launch the patched game. Expected: Hachimi Edge first-time setup dialog appears
8. Skip/dismiss translation setup (we don't need translations for the English client). **Do not install JP translations** — this will cause corrupted textures on the Global client.
9. Verify the Hachimi Edge overlay is accessible (triple-tap top-left on Android)
10. Verify the game boots to the title screen and can log in (use Data Link or Cygames ID — Google Play login is disabled in patched APKs)
11. Check where Hachimi created its config directory — is it at `/sdcard/Android/media/com.cygames.umamusume/hachimi/` or does it still reference the JP path?
12. Play through the tutorial to confirm basic gameplay works

**Deliverable**: A document confirming:
- Hachimi Edge loads successfully on the Global APK (yes/no, and if no, what error)
- The Hachimi overlay is functional
- The game connects to servers and gameplay works
- **Package name verification**: Confirm the Global APK uses `com.cygames.umamusume` and document all paths that differ from the JP version (`jp.co.cygames.umamusume`)
- **Hachimi config path**: Where did Hachimi create its config directory? Does it correctly use the Global package name, or does it hardcode the JP one?
- **UmaPatcher behavior**: Did UmaPatcher accept the Global APK directly, or did you need workarounds? Document the exact steps that worked.
- MuMuPlayer macOS resource usage (RAM, CPU) during gameplay
- The exact APK version used and Hachimi Edge version installed
- The ADB connection method used (`adb devices` output, port forwarding test)
- The data directory path for the installed Global game (run `adb shell pm path com.cygames.umamusume` and `adb shell ls /data/data/com.cygames.umamusume/` if accessible)

**Failure mode**: If Hachimi Edge's hooks fail to initialize on the Global binary (e.g., il2cpp metadata resolution fails due to different obfuscation), document the error and we'll need to investigate whether the Global client uses a different Unity configuration. If the failure is specifically path-related (wrong package name in Hachimi's config resolution), that's a fixable issue — document which paths are wrong and what they should be.

---

### WS-2: Plugin Scaffold

**Owner**: Agent with Rust development experience
**Duration**: 2-3 days
**Blocking**: Nothing (can start immediately)
**Blocked by**: Nothing (does not need the emulator — can be tested on desktop first, deployed to Android later)

**Goal**: A minimal Hachimi Edge plugin crate that compiles to an ARM64 `.so`, conforms to the Hachimi plugin API, registers itself on load, and exposes a placeholder in the Hachimi GUI.

**Context for the agent**:

You are writing a Rust shared library (.so) that will be loaded as a plugin by Hachimi Edge, a game modding framework for Uma Musume: Pretty Derby. Hachimi Edge is itself written in Rust. The plugin runs inside the game process on Android (ARM64).

The plugin API documentation: https://hachimi.noccu.art/docs/plugins/about
Plugins are `.so` files on Android, `.dll` on Windows. They integrate with Hachimi through a defined API. Plugins can: hook and inspect il2cpp functions, add GUI elements, and load Android DEX code.

The Hachimi Edge source is at https://github.com/kairusds/Hachimi-Edge. The plugin API types are in `src/core/plugin_api.rs`. Hachimi uses `dobby-rs` for inline hooking (a Rust wrapper around the Dobby hooking library by jmpews). The `Interceptor` struct in `src/core/mod.rs` manages hooks.

We don't need any game-specific functionality yet — just the build chain and API registration.

**Steps**:
1. Create a Rust crate with `crate-type = ["cdylib"]`
2. Add the `aarch64-linux-android` target: `rustup target add aarch64-linux-android`
3. Configure Android NDK cross-compilation in `.cargo/config.toml`
4. Implement the Hachimi plugin entry point (refer to plugin_api.rs for the expected exported symbols)
5. Register a menu item in Hachimi's GUI (e.g., "CarrotRelay: Inactive") to confirm the plugin loads
6. Implement a configuration loader that reads from the Hachimi plugins directory. The path varies by package: `/sdcard/Android/media/com.cygames.umamusume/hachimi/plugins/carrot_relay.json` for Global, or `jp.co.cygames.umamusume` for JP. The plugin should detect the package name at runtime from its own process context rather than hardcoding it.
7. Add a `dobby-rs` dependency and verify it compiles for aarch64-linux-android (this is the hooking library we'll use in Phase 2)
8. Build and verify the `.so` output

**Deliverable**: A git repository containing:
- `carrot_relay/Cargo.toml` with all dependencies
- `carrot_relay/src/lib.rs` — plugin entry point with Hachimi API registration
- `carrot_relay/src/config.rs` — JSON config loader
- `carrot_relay/.cargo/config.toml` — cross-compilation config
- `carrot_relay/build.sh` — one-command build script
- `README.md` documenting the build requirements (Rust version, NDK version, targets)
- The compiled `libcarrot_relay.so` artifact for ARM64

The plugin does NOT need to hook anything yet. It just needs to load inside Hachimi and prove the API integration works.

**Failure mode**: If the Hachimi plugin API is insufficiently documented or requires private symbols from Hachimi's own binary, document what's missing. We may need to build against Hachimi Edge's source directly rather than treating the API as a stable interface.

---

### WS-3: Schema Research

**Owner**: Agent with web research + data analysis skills (no emulator needed)
**Duration**: 2-3 days
**Blocking**: Nothing (can start immediately)
**Blocked by**: Nothing

**Goal**: A comprehensive mapping of the msgpack packet schema used in Uma Musume server responses, with typed Python dataclasses ready for the bot to consume.

**Context for the agent**:

Uma Musume: Pretty Derby communicates with Cygames' servers using MessagePack (msgpack) over HTTPS. The traffic is encrypted and compressed by `libnative.so`, but tools like CarrotJuicer intercept the decrypted/decompressed msgpack payloads. We need to understand the structure of these packets so our bot can parse them into a typed game state.

The msgpack schema is not officially documented, but it has been reverse-engineered by the community. Your job is to compile and consolidate this knowledge.

**Sources to investigate** (in priority order):
1. **UmaLauncher source code** (https://github.com/KevinVG207/UmaLauncher) — This Python application parses CarrotJuicer packets to display training info, event helpers, and training logs. Its source code is the most direct documentation of the packet schema. Look for files related to packet parsing, training state, event handling.
2. **UmaLauncher Training Analyzer Documentation** (https://github.com/KevinVG207/UmaLauncher/blob/main/Training_Analyzer_Documentation.md) — Documents the fields exported to CSV from training runs, which maps directly to packet fields.
3. **Hakuraku source code** (https://github.com/SSHZ-ORG/hakuraku) — Web tool for inspecting CarrotJuicer packets. The JavaScript source contains packet parsing logic, especially for race data.
4. **Hakuraku web UI** (https://hakuraku.sshz.org/#/carrotjuicer) — Can load raw `.msgpack` files and display their structure interactively.
5. **cjedb** (https://github.com/CNA-Bld/cjedb) — External data file used by CarrotJuicer for enriching output with character/card names. Its schema reveals what IDs appear in packets.
6. **master.mdb schema** — The game's local SQLite database. Community tools (UmaMusumeAPI, UmaViewer) have documented its table structures. Packet fields reference IDs from this database.

**Deliverable**: A Python module containing:

```
carrot_relay_schema/
├── __init__.py
├── packets.py          # Top-level packet routing (which packet type contains what)
├── training_state.py   # Dataclasses for per-turn training state
├── events.py           # Dataclasses for event data (event ID, choices, outcomes)
├── skills.py           # Dataclasses for skill lists, costs, conditions
├── race.py             # Dataclasses for race results, race_scenario parsing
├── support_cards.py    # Dataclasses for support card state, bond levels
├── career.py           # Dataclasses for career goals, fan counts, turn info
├── enums.py            # Enums for mood, training type, race distance, etc.
├── parser.py           # Main parser: raw msgpack dict → typed GameState
└── README.md           # Schema documentation with example packet structures
```

Each dataclass should have:
- Type annotations for every field
- Docstrings explaining what the field represents in game terms
- The raw msgpack key name as a comment (e.g., `# msgpack key: "speed"`)
- Optional fields marked as `Optional[T]` with notes on when they're present vs. absent

Include example raw msgpack structures (as JSON) for at least: a training turn response, an event trigger response, a race result response, and a skill purchase response.

**Failure mode**: If the schema can't be fully determined from public sources alone (some fields may be opaque IDs with no documentation), document what's known vs. unknown and mark unknown fields as `Any` in the dataclasses. We'll fill in gaps during Phase 2 when we have live packet captures.

---

## GATE 1: Phase 1 Completion Criteria

All three streams must complete before Phase 2 begins:
- **WS-1**: Hachimi Edge confirmed working on Global APK in MuMuPlayer, OR a documented failure with a remediation plan
- **WS-2**: Plugin `.so` compiles for ARM64, passes static analysis, ready to deploy
- **WS-3**: Schema dataclasses exist with at least training state, events, and skills mapped

The manager agent merges the three outputs:
- Takes the environment from WS-1
- Deploys the plugin `.so` from WS-2 into the patched APK via UmaPatcher
- Confirms the plugin loads inside the running game (Hachimi GUI shows the menu item)

---

## PHASE 2: Core Implementation

Three parallel streams that build on Phase 1 artifacts. WS-4 is the critical path.

---

### WS-4: Hook Implementation

**Owner**: Agent with Rust + reverse engineering experience
**Duration**: 3-5 days
**Blocking**: WS-2 (plugin scaffold), WS-1 (environment for testing)
**Blocked by**: Gate 1

**Goal**: The plugin hooks `LZ4_decompress_safe` (or equivalent) in `libnative.so`, captures decompressed msgpack buffers, and writes them to files on the Android filesystem for verification.

**Context for the agent**:

You are extending the CarrotRelay plugin (from WS-2) to hook a native function inside `libnative.so`, a shared library used by Uma Musume for network packet encryption/compression. The hook should intercept the output of the LZ4 decompression function — at that point, the data is decrypted and decompressed, yielding raw msgpack bytes.

CarrotJuicer (https://github.com/CNA-Bld/CarrotJuicer) does exactly this on Windows via DLL hooking. The Windows version hooks `LZ4_decompress_safe` or `LZ4_decompress_safe_usingDict` in `libnative.dll`. The Android version (Riru-CarrotJuicer) does the same on `libnative.so` via PLT/GOT hooking through Zygisk.

We are doing the equivalent from within a Hachimi Edge plugin, using `dobby-rs` for inline hooking. We are already loaded in the game process (Hachimi handles that), so we just need to find and hook the function.

**Steps**:
1. Locate `libnative.so` in the game's process memory. On Android, use `dl_iterate_phdr` or parse `/proc/self/maps` to find the base address.
2. Attempt to resolve the target function by symbol name:
   - Try `dlsym` for `LZ4_decompress_safe`, `LZ4_decompress_safe_usingDict`, and `LZ4_decompress_fast_usingDict`
   - If symbols are exported, this gives us the function pointer directly
3. If symbols are stripped, implement a byte-pattern signature scan over the `.text` section of `libnative.so`. The LZ4 decompression function has a distinctive prologue. Reference the Windows CarrotJuicer source for known patterns.
4. Hook the resolved function pointer using `dobby-rs`:
   - The wrapper calls the original function
   - If the return value is > 0 (success), copy `result` bytes from the output buffer
   - Write the copied bytes to a timestamped file in `/sdcard/Android/media/jp.co.cygames.umamusume/carrot_relay/`
5. Verify: launch the game, perform actions that trigger server responses (enter a Career, train, trigger an event), check that `.msgpack` files appear in the output directory
6. Validate: pull the files via ADB and inspect with `msgpack2json` or Hakuraku to confirm they contain valid, parseable game state

**Deliverable**:
- Updated `carrot_relay/src/hook.rs` with the complete hook implementation
- A document listing:
  - The symbol name that was found (or the signature pattern used)
  - The function's address offset within `libnative.so`
  - Whether the Global client's `libnative.so` symbols differ from JP
  - Sample captured packets (3-5 `.msgpack` files from different game actions)
- Any discovered edge cases (e.g., multiple decompression calls per response, compressed vs. uncompressed paths, request vs. response differentiation)

**Failure mode**: If `libnative.so` strips all relevant symbols AND the LZ4 byte-pattern signature doesn't match (different compiler, different version), escalate to the il2cpp-level hook approach (Option B in the spec). Document the `libnative.so` binary analysis (symbol table dump, section headers, any identified functions) so Option B can pick up where this left off.

---

### WS-5: Python Receiver & Transport

**Owner**: Agent with Python networking experience
**Duration**: 2-3 days
**Blocking**: Nothing in Phase 2 (can start as soon as Gate 1 passes)
**Blocked by**: Gate 1 (needs the schema from WS-3)

**Goal**: A Python module that receives msgpack packets from the plugin (via file polling initially, Unix socket later) and produces parsed `GameState` objects.

**Context for the agent**:

You are building the host-side (Mac) component that receives game state packets from the CarrotRelay plugin running inside the Android emulator. The plugin captures decrypted msgpack server responses and makes them available to us. Your module consumes these packets and produces typed Python objects the bot's decision engine can use.

The schema dataclasses from WS-3 define the target types. The transport will evolve in two stages:
1. **Stage 1 (file polling)**: The plugin writes `.msgpack` files to a shared directory. You poll this directory via ADB and parse new files. This is the initial integration path because it's simple and doesn't require socket plumbing.
2. **Stage 2 (socket streaming)**: The plugin writes to a Unix domain socket. ADB forwards this to a TCP port on the host. You connect to localhost and receive a stream of length-prefixed msgpack frames. This is the production path for low-latency operation.

Implement both stages. Stage 1 is the priority for Phase 2 integration; Stage 2 is the priority for Phase 3 production use.

**Steps**:
1. Implement `FilePoller`:
   - Watches a local directory (populated by `adb pull` or `adb shell` polling)
   - Detects new `.msgpack` files by timestamp
   - Reads and parses each file with `msgpack.unpackb()`
   - Yields parsed dicts
2. Implement `SocketReceiver`:
   - Connects to `localhost:4693` (ADB-forwarded from the emulator)
   - Reads length-prefixed frames (4-byte big-endian length, then payload)
   - Parses each frame with `msgpack.unpackb()`
   - Yields parsed dicts
   - Handles reconnection on disconnect (the game may restart)
3. Implement `PacketRouter`:
   - Takes a raw parsed dict from either transport
   - Identifies the packet type (training state update, event, race result, etc.) based on top-level keys
   - Dispatches to the appropriate parser from the WS-3 schema module
   - Produces a typed `GameState` object (or a delta to the existing state)
4. Implement `StateManager`:
   - Maintains the current `GameState` as an evolving object
   - Applies packet deltas (e.g., a training turn response updates stats, energy, turn number)
   - Provides a `.snapshot()` method returning the complete current state
   - Thread-safe (the receiver runs in a background thread, the decision engine reads from the main thread)
5. Write unit tests using captured `.msgpack` sample files (from WS-3's example packets, or mocked)

**Deliverable**: A Python package:
```
uma_trainer/perception/carrotjuicer/
├── __init__.py
├── file_poller.py      # Stage 1: file-based packet ingestion
├── socket_receiver.py  # Stage 2: socket-based packet streaming
├── packet_router.py    # Identifies packet type, dispatches to schema parser
├── state_manager.py    # Maintains evolving GameState from packet stream
├── adb_bridge.py       # ADB helper: pull files, forward ports, check connection
└── tests/
    ├── test_file_poller.py
    ├── test_socket_receiver.py
    ├── test_packet_router.py
    ├── test_state_manager.py
    └── fixtures/           # Sample .msgpack files for testing
```

**Failure mode**: If the schema from WS-3 is incomplete (some packet types not yet mapped), implement the `PacketRouter` to log unrecognized packet types with their raw structure, and pass them through as `dict` rather than failing. We'll fill in the schema gaps iteratively.

---

### WS-6: State Assembly & GameState API

**Owner**: Agent with Python data modeling experience
**Duration**: 2-3 days
**Blocking**: WS-3 (schema dataclasses)
**Blocked by**: Gate 1

**Goal**: A clean, well-documented `GameState` API that the decision engine consumes, abstracting away whether the data came from CarrotJuicer packets or OCR fallback.

**Context for the agent**:

The bot's decision engine needs a single `GameState` object to make decisions from. Currently this comes from OCR. We're adding a CarrotJuicer packet path that will be more accurate. Both paths should produce the same `GameState` interface so the decision engine doesn't need to know or care which perception pipeline is active.

The schema dataclasses from WS-3 define the raw packet structure. Your job is to build the abstraction layer above that — the `GameState` class that the rest of the bot interacts with.

**Steps**:
1. Define the canonical `GameState` dataclass that represents everything the decision engine needs:
   - Training tile evaluation data (stats per tile, support cards, rainbow/gold/hint flags)
   - Trainee current stats, energy, mood, motivation
   - Career progress (turn number, goals, fan count)
   - Event state (when an event is active: event ID, text, choices)
   - Skill state (owned skills, available skills, skill points)
   - Bond levels per support card
   - Screen type (training, event, race, skill shop, etc.)
   - Button positions (for click targeting — this comes from OCR, not packets)
2. Define a `PerceptionProvider` protocol/ABC:
   ```python
   class PerceptionProvider(Protocol):
       async def get_state(self) -> GameState: ...
       async def wait_for_update(self) -> GameState: ...
       def is_healthy(self) -> bool: ...
   ```
3. Implement `CarrotJuicerProvider` — wraps the `StateManager` from WS-5
4. Implement `OCRProvider` — wraps the existing OCR pipeline (stub implementation that raises `NotImplementedError` for now, to be filled in by the OCR team)
5. Implement `FallbackProvider` — tries CarrotJuicer first, falls back to OCR if CJ is unhealthy (no packets received for N seconds)
6. Write comprehensive docstrings and type annotations. The decision engine team will code against this API without reading the implementation.

**Deliverable**: A Python package:
```
uma_trainer/perception/
├── __init__.py
├── game_state.py       # Canonical GameState dataclass + sub-dataclasses
├── provider.py         # PerceptionProvider protocol + FallbackProvider
├── cj_provider.py      # CarrotJuicerProvider implementation
├── ocr_provider.py     # OCRProvider stub
└── tests/
    ├── test_game_state.py
    ├── test_fallback.py
    └── fixtures/
```

**Failure mode**: If the schema from WS-3 reveals that some critical fields (e.g., training tile projected stat gains) are not available in the packets, document this in the `GameState` class with clear comments about which fields are CJ-only, OCR-only, or available from both. The `FallbackProvider` may need to merge data from both sources for certain fields.

---

## GATE 2: Phase 2 Completion Criteria

All three streams must complete:
- **WS-4**: Plugin captures valid msgpack packets from live gameplay (confirmed via file inspection)
- **WS-5**: Python receiver can ingest captured packets (at minimum via file polling) and produce parsed dicts
- **WS-6**: GameState API is defined and the CarrotJuicerProvider connects to the receiver

The manager agent performs end-to-end integration:
- Deploys the hooked plugin into the patched APK
- Starts the Python receiver on the host
- Launches the game, enters a Career, plays a turn
- Verifies the `GameState` object on the Python side matches what's visible on screen

---

## PHASE 3: Integration & Hardening

Single stream, but can be split among agents by subsystem.

---

### WS-7: Production Integration

**Owner**: Agent(s) with access to the full bot codebase
**Duration**: 3-5 days
**Blocking**: Gate 2
**Blocked by**: All Phase 2 work

**Goal**: The bot runs full Career Mode training runs using CarrotJuicer packet data as its primary perception source, with graceful OCR fallback.

**Sub-tasks (dividable among agents)**:

**WS-7a: Socket transport upgrade** — Replace file polling (WS-5 Stage 1) with the Unix domain socket transport (WS-5 Stage 2) as the production path. Implement the corresponding Rust side in the plugin. Test with ADB forward. Verify latency is <50ms from server response to GameState availability on the host.

**WS-7b: Decision engine integration** — Wire the `FallbackProvider` into the existing decision engine. Replace all direct OCR reads with `GameState` queries. Verify the scoring system, event matching, and skill purchase logic work with CJ-sourced data. Key change: event matching can now use the exact event ID from the packet (numeric lookup) instead of fuzzy text matching.

**WS-7c: Fallback transition logic** — Implement health monitoring on the CJ pipeline. If no packets arrive for 10 seconds during active gameplay, log a warning. After 30 seconds, switch to OCR. If CJ packets resume, switch back. This transition must be seamless — the bot should not crash, stall, or make a bad decision during the switch.

**WS-7d: Game update resilience** — Document and automate the game update process: detect that a game update is available, download the new APK, re-patch with UmaPatcher (preserving the plugin), reinstall, and resume. This doesn't need to be fully automated in v1 but should be scripted to the point where a human runs one command.

**WS-7e: End-to-end testing** — Run 5+ full Career Mode runs with the CJ pipeline active. Log every decision alongside the packet data that informed it. Compare decision quality to OCR-based runs. Document any packet fields that were unexpectedly absent or structured differently than WS-3 predicted.

**Deliverable**: The bot completes Career Mode runs autonomously with CJ as the primary perception source. A report documenting: decision accuracy vs. OCR baseline, packet delivery latency, any schema gaps discovered, and the game update re-patching procedure.

---

### Dependency Graph (Summary)

```
WS-1 (Env Setup)      ─┐
WS-2 (Plugin Scaffold) ─┼── GATE 1 ── WS-4 (Hook Impl)     ─┐
WS-3 (Schema Research) ─┘              WS-5 (Python Receiver) ─┼── GATE 2 ── WS-7 (Integration)
                                        WS-6 (GameState API)   ─┘
```

**Critical path**: WS-1 → WS-4 → WS-7 (environment → hook → integration)
**Parallel capacity**: Up to 3 agents in Phase 1, up to 3 in Phase 2, up to 5 sub-agents in Phase 3
**Total elapsed time**: ~2.5-3 weeks with parallel execution (vs. ~5-6 weeks sequential)
**Total agent-hours**: ~15-20 days of work across all streams
