# Overnight Frida session — 2026-04-17 ~03:00-03:30

## Summary
Root hiding: still holding after 12GB-RAM emulator restart (`/system/bin/.su_stash`, `/system/xbin/.su_stash`, Kitsune package present but idle). Game reached home screen, ptrace child detected.

Frida eviction approach (`scripts/frida_attach_evict.py`): **FAILED** — the ptracer is a dead-man's-switch. Killing it takes the parent down too.

## What was tried
1. Started frida-server on 127.0.0.1:5555 via `scripts/frida_start.sh` — pid 5319, healthy.
2. Dry-run on live game (main pid 4016, ptrace child pid 4122):
   ```
   [*] com.cygames.umamusume processes: [4016, 4122]
       pid=4016  TracerPid=4122
       pid=4122  TracerPid=0
   [*] game main pid=4016, self-ptrace child pid=4122
   ```
   Dry-run identified the child correctly.
3. Real run: killed pid 4122. The script then crashed reading `/proc/4016/status` because **4016 was already gone**.

## Post-mortem from logcat
```
03:28:51.348 I/ActivityManager: Process com.cygames.umamusume (pid 4016) has died: fg TOP
03:28:51.349 I/Zygote:          Process 4016 exited due to signal 9 (Killed)
```
No `Killing 4016:` line from ActivityManager; no lmkd entry. The parent SIGKILL'd itself after noticing the ptracer vanished (most likely via a monitoring thread that calls `kill(getpid(), SIGKILL)` when `waitpid` on the ptracer returns child-died). Turnaround was sub-second — the parent died before my script's verify step could read `/proc`.

## Implication
Straight eviction is dead on arrival for Uma Musume Global. The anti-debug child isn't just a PTRACE_ATTACH-and-idle — it's part of a mutual watchdog.

## Next avenues (for morning, not tonight)
**A. Attach-to-the-ptracer trick.** ~~The ptracer has TracerPid=0, so Frida CAN attach to it. From inside it, issue `ptrace(PTRACE_DETACH, <parent>, 0, 0)` to release the parent.~~ **TESTED — FAILED.** Script `scripts/frida_detach_via_tracer.py` attempted attach to pid 5481. `device.attach(5481)` returned "agent connection closed unexpectedly" **before** the injected RPC could run. Post-mortem logcat: `Process 5406 exited cleanly (0)` — not SIGKILL'd, but the game deliberately self-terminated. So when **anything** attempts to ptrace-inject into the watchdog child, the game sees it and bows out gracefully. The watchdog is not just checking "is my ptracer alive" — it's checking "is my ptracer uncompromised."

Additional data point from logcat: the watchdog child's Android process name is `.umamusume:tab1` — the game's WebView subprocess doubles as the anti-debug ptracer. Killing it takes the browser child down first, then parent SIGKILLs itself (signal 9). Frida-attaching it triggers a different code path (clean exit 0).

**B. Hook the watchdog before it fires.** Requires very early injection — before lib__4e06__.so finishes loading. Options:
   - Spawn-mode Frida with LD_PRELOAD of gadget (we tried spawn-mode before, SIGABRT at 132ms = lib__4e06__ detecting Frida. Root hiding might not change that.)
   - A native .so dropped into the app's library dir with `ctor` that patches things before anti-tamper runs. Needs writable app_lib dir, probably needs root re-hidden.

**C. Gadget injection in a patched APK.** Previously hit SIGABRT at 132ms (anti-tamper rejecting re-signed APK). That was before root hiding. Worth retesting because the 132ms abort may itself have been a root-triggered check, not a signature check. Low effort to try: `bash scripts/build_patched_apk.sh` (if it still exists) or just re-run UmaPatcher on the current APK, then reinstall.

**D. Skip Frida entirely.** The original spec path was Hachimi-Edge Zygisk module, which intercepts at the engine level (before anti-tamper even loads its detection). That requires Magisk, which requires a working boot.img on MuMu Pro Mac — the blocker from earlier in the day. Revisit if C fails.

**My recommendation for morning:** C first (30 min test). Then D (substantial work, but it's the spec-canonical approach).

## Final observations
- The two crash modes differ:
  - Kill ptracer → `signal 9 (Killed)` (parent self-kills or kernel SIGKILLs it)
  - Frida-attach ptracer → `exited cleanly (0)` (parent gracefully exits)
- Both kill the main process within ~500ms.
- So the watchdog has two separate detection paths for "tracer dead" vs "tracer compromised."

## Approach C tested — FAILED
Installed the previously-patched splits (`data/ws1_work/installed-*.apk`, signer `f3544555` = UmaPatcher debug cert). Launched. Crashed exactly like before:

```
11:27:17.452  ActivityTaskManager: START com.cygames.umamusume
11:27:17.601  libc: Fatal signal 6 (SIGABRT), code -1 (SI_QUEUE) pid 4632
11:27:17.689  Zygote: Process 4632 exited due to signal 6 (Aborted)
```
~149ms from launch to SIGABRT, previously was 132ms, same order. Backtrace is just `abort+164` / `__libc_current_sigrtmin+4` in libc — no game-lib frames, meaning the abort fires before any symbol-resolvable code. This is consistent with lib__4e06__.so's constructor or its called-before-JNI init running a deliberate `abort()`.

**Root hiding was active throughout.** Therefore anti-tamper at 132–149ms is **signature-based** (or hash-based on base.apk), NOT root-based. Memory entry `project_global_tamper_check.md` is confirmed.

## State when stopping
- Patched APK uninstalled, stock Play Store APK (`global-*.apk`, signer `c30bb2fd`) reinstalled.
- Game boots cleanly on stock (main pid 4848, ptrace child pid 4935 at 11:27-ish).
- **User wakes to a working install but has lost the 9GB download state and login state** — unavoidable because testing patched APKs required an uninstall (same package name, different signer). The splash/title → login → re-download will take a while.
- frida-server still running on device. Root hiding still intact.

## Wall-hit summary
Every practical Frida injection vector for Uma Global now tested and blocked:
- Spawn-mode Frida: earlier SIGABRT at 132ms (anti-tamper detects Frida gadget) — unchanged by root hiding.
- Eviction attach: parent self-kills when ptracer dies.
- Ptracer-mediated detach: parent self-exits when ptracer is Frida-attached.
- Re-signed APK + Hachimi proxy: SIGABRT ~149ms (signature check).

**The one remaining path is approach D: Hachimi-Edge Zygisk via Magisk.** That requires Magisk bootstrap on MuMu Pro Mac, which was the blocker earlier in the day (Kitsune Direct Install fails: "Unable to detect target image" because MuMu Pro Mac qcow2 has no /dev/block boot partition).

Re-opening D tomorrow will require either:
1. Synthesizing a boot.img for Kitsune to patch (invasive, risky)
2. Manually extracting Magisk's init-riru + zygisk bits and dropping them into /system, then bootstrapping via init.d-style startup — requires significant reverse engineering of Kitsune's patcher
3. Giving up on MuMu and moving to a real rooted Android device with a real boot partition
4. Pivoting away from Hachimi entirely and building custom tooling that doesn't need Magisk (e.g., a custom /system-installed native hook that runs in the app's process via ld.config.txt overrides — plausible but also non-trivial)

## Current emulator state (as of 03:30)
- ADB bridge: 127.0.0.1:5555, both the tcp bridge and emulator-5554 are online
- Root hiding: intact
- Kitsune: disabled-user state survived restart (to be reconfirmed)
- frida-server: running at pid 5319
- Game: **DEAD**. Restarted below.
- Scheduled CronCreate wake-up at 03:53: will hit a home screen (probably) but has nothing useful to do; should be skipped or repurposed.

---

# 2026-04-17 afternoon — BlueStacks pivot & Hachimi Zygisk plan

## What changed since the overnight session

We moved off MuMu and onto **BlueStacks Air** (macOS), rooted via patched `initrd_hvf.img`. This gave us a real `/data/adb/` and full Magisk+Zygisk. With that stack we got Frida attach working for the first time on Uma Global (see `project_zygiskfrida_bypass.md`).

**Verified working end-to-end on BlueStacks (2026-04-17):**
- Kitsune Magisk R6687BB53, `magiskd` pid 359, `zygisk=1`, denylist=1
- ZygiskFrida v1.9.0 module loaded; remaps libgadget.so to anonymous pages
- `start_up_delay_ms: 3000` in `/data/local/tmp/re.zyg.fri/config.json` — load-bearing, delays Frida past Uma's 230ms anti-Frida scan
- Root hidden from Uma: `/system/bin/.su_stash` + `/sbin/.su_stash` symlinks, Kitsune APK `pm disable-user`
- Frida host attach over `adb forward tcp:27042` → `add_remote_device`
- Module enumerate of libnative.so, libil2cpp.so, libmain.so, lib__4e06__.so, libunity.so all work

## Today's dead end — direct libnative.so hooking

Attempted WS-4: install `Interceptor.attach` on `LZ4_decompress_safe_ext` (exported from libnative.so, address 0x70b1eea86c).

Outcome:
- Idle-attach (no hooks past ptrace+dlopen watcher): game lived 95s+
- With LZ4 Interceptor trampoline: **game SIGSEGVs within 1-2 seconds**
- Exit traps on `_exit/exit/abort/_Exit/pthread_exit/syscall/kill/tgkill/tkill/raise` all installed — **none fired**
- Logcat shows `signal 11 (SIGSEGV), code 2 (SEGV_ACCERR) fault addr 0x72372d6482, pc 0x70cd94e834 <anonymous:0x70cd930000>` — crash inside an anonymous JIT region (IL2CPP), not libc

Interpretation: Uma's anti-cheat scans libnative.so executable memory on a timer. When it sees our trampoline bytes, instead of calling `abort()`, it deliberately corrupts a pointer or jumps to bad memory — crashes look like random bugs. Classic anti-RE behaviour.

**Implication:** direct Frida Interceptor patching of libnative.so is a blocked path. Any future Frida work has to hook either (a) functions *above* libnative (IL2CPP land) or (b) use a non-patching technique (Stalker events, hardware breakpoints).

## Where this leaves us

Frida-over-Zygisk is solid but limited to hook points outside the protected native blob. For full game-state perception we need something that runs inside the app's address space with the authority to intercept game engine calls without leaving trampoline bytes in libnative. That is exactly what Hachimi was designed to do.

Task #5 previously abandoned Hachimi via UmaPatcher (libmain.so swap) because re-signed APKs SIGABRT at 132ms from `lib__4e06__.so`'s signature check. But Hachimi has a **second install path that doesn't re-sign the APK**: a Zygisk module. Not shipped as a release artifact — but present in source.

## Hachimi Zygisk — the plan

**Source reconnaissance (verified today):**
- `Hachimi-Hachimi/Hachimi` has `src/android/zygisk/` with `main.rs` (Module lifecycle), `internal.rs` (Zygisk ABI bindings), `mod.rs` (struct defs)
- `src/android/mod.rs` declares `mod zygisk;` unconditionally — i.e. `zygisk_module_entry` is exported from the same `libmain.so` the UmaPatcher path uses
- **The JP-only gate is a single branch in `zygisk/main.rs`:**
  ```rust
  (*this).is_game = match game_impl::get_region(&package_name) {
      Region::Japan => true,
      _ => false
  };
  ```
- `game_impl::get_region` already maps `com.cygames.umamusume → Region::Global`
- The Zygisk hook surface is **region-agnostic**: `do_dlopen` in `linker64` and `JNINativeInterface::RegisterNatives`. Nothing game-specific until `libil2cpp.so` loads and the on_dlopen callback fires — at which point region-specific IL2CPP hooks happen inside Hachimi core, with failures swallowed by `unwrap_or_else(|e| { error!(...) })`.

**Plan phases:**

| # | Work | Time | Notes |
|---|---|---|---|
| A | Install rustup + `aarch64-linux-android` target + Android NDK + `cargo-ndk` | ~30 min, ~2 GB disk | One-time host setup |
| B | Clone Hachimi-Edge (`kairusds/Hachimi-Edge`) locally | 1 min | Edge first because it claims Global support on Steam — may already carry Global-compatible IL2CPP offsets |
| C | One-line patch: allow `Region::Global` alongside `Region::Japan` in `zygisk/main.rs` | trivial | If Edge already allows Global, skip this |
| D | `cargo ndk -t arm64-v8a build --release` → `libmain.so` with `zygisk_module_entry` | 5 min | |
| E | Package as Magisk module: `module.prop` + `zygisk/arm64-v8a.so` | 5 min | |
| F | Push `/data/adb/modules/hachimi/`, reboot BlueStacks | 5 min | Keep root hidden intact |
| G | Launch Uma, tail logcat for `hachimi` tag | unknown | First success criterion: "module loaded + hook::init ran without panic" |

**Why Hachimi-Edge first:** Edge is the actively maintained fork (2026) and claims Global support on Steam. Offsets for Global may already be in place. If Edge works, we skip the "figure out IL2CPP offsets for Global" subproject. If Edge doesn't work on Android-Global, fall back to upstream Hachimi + manual Global offset work.

**Known risks:**
1. IL2CPP offsets for Global-on-Android may differ from Global-on-Steam — some features no-op or mis-hook. First-attempt success is "loads cleanly," not "translation working."
2. Zygisk module loads into every fork, not just Uma. `pre_app_specialize` gates on package name so damage is contained. Still — test carefully.
3. BlueStacks reboot is quirky; may need full VM restart via BlueStacks UI, not `adb reboot`.
4. The root-hiding we applied (su stash + Kitsune disable) composes fine with Zygisk modules — Zygisk runs at zygote-fork, independent of the manager APK — but we may need brief un-hiding if a module zip wants to flash through Magisk's own install flow.

## Option catalogue (for completeness)

The choices we've considered and where they stand right now:

| Option | Status | Verdict |
|---|---|---|
| Frida Interceptor on libnative.so | Blocked — 60s SIGSEGV via integrity check | DEAD |
| Frida Interceptor on IL2CPP functions (in anonymous JIT region) | Unverified, plausible — JIT region probably not integrity-checked | Fallback if Hachimi fails |
| Frida Stalker (event-only, no code patching) | Untested; probably still triggers memory-read detection | Unlikely |
| Hachimi via UmaPatcher (libmain.so swap + APK re-sign) | Blocked by 132ms signature check in `lib__4e06__.so` | DEAD |
| **Hachimi-Edge Zygisk module (build ourselves)** | Feasible; source exists, build env missing | **CURRENT PLAN** |
| Upstream Hachimi Zygisk module (build ourselves) | Feasible; fallback from Edge | Fallback |
| LSPatch (app-scoped injection without re-sign) | Untested; promising alternative | Held in reserve |
| Frida via hardware breakpoints (ARM64 has 4) | Untested; complex | Held in reserve |

## Immediate next step (with user approval)

User asked: "Update your .md first with the new plan and our current options. Then try Hachimi Edge first." Going to Phase A now — install rustup + NDK — then phases B–G against Hachimi-Edge.

## 2026-04-17 evening — Hachimi Zygisk is installed and running. Uma dies via unknown exit path 1.7–2.7s after `Hooking finished`.

### What works (confirmed end-to-end)
1. Host toolchain: rustup + `aarch64-linux-android` target + `/opt/homebrew/share/android-ndk` + cargo-ndk. Built `libhachimi.so` (15.8 MB) from `kairusds/Hachimi-Edge` cleanly.
2. Exports verified with `llvm-nm`: `zygisk_module_entry`, `zygisk_companion_entry`, `JNI_OnLoad` all present.
3. Packaged as Magisk module at `/data/adb/modules/hachimi/{module.prop,zygisk/arm64-v8a.so}`. Kitsune's manager recognized it.
4. **Hachimi IL2CPP hooks install successfully on Uma Global.** Selected log lines:
   ```
   I/Hachimi: hachimi::core::hachimi: Game region: Global
   I/Hachimi: hachimi::android::hook: Hooking __dl__Z9do_dlopenPKciPK17android_dlextinfoPKv at 0x7136b5c6ec
   I/Hachimi: hachimi::core::hachimi: Got il2cpp handle
   I/Hachimi: hachimi::il2cpp::hook::Cute_Core_Assembly::SafetyNet: new_hook!: GetSafetyNetStatus
   I/Hachimi: hachimi::il2cpp::hook::Cute_Core_Assembly::Device: new_hook!: IsIllegalUser
   I/Hachimi: hachimi::il2cpp::hook: Hooking finished
   I/Hachimi: hachimi::core::hachimi: Character database loaded successfully.
   I/Hachimi: hachimi::core::hachimi: Skill info loaded successfully.
   ```
5. Hachimi + ZygiskFrida coexist in the same process. Frida gadget listens on `127.0.0.1:27042` as expected.

### What fails (the new blocker)
Uma exits ~1.7–2.7s after `Hooking finished` every time, with the signature:
```
I/Zygote: Process <pid> exited cleanly (0)
```
No `SIGABRT`, no `SIGSEGV`, no Android `Force finishing` line, no Hachimi `ERROR`/`panic` log line before death.

### Two failure modes observed and resolved/diagnosed
| Config | Outcome | Resolution |
|---|---|---|
| Default Hachimi (GUI enabled) | SIGABRT 146ms after `Got nativeInjectEvent address` | Set `disable_gui: true` in `/sdcard/Android/media/com.cygames.umamusume/hachimi/config.json` → suppresses the `RegisterNatives` hook → SIGABRT gone |
| `disable_gui: true` | Clean `exit(0)` ~1.7s after `Hooking finished` | **Unresolved** |

### Exit-trap diagnostic: death path is NOT libc
Attached via Frida gadget, installed `installExitTraps()` (hooks `exit`, `_exit`, `_Exit`, `abort`, `pthread_exit`, `syscall(SYS_exit_group)`, `kill`, `tgkill`, `tkill`, `raise`). All reported installed with concrete addresses. Uma died; **none fired**.

Implication: the exit path used does not go through any libc.so-exported symbol. Candidates:
- Raw `svc #0` inline assembly in `libunity.so` / `libil2cpp.so` (our syscall hook only catches the libc `syscall()` wrapper, not inlined syscalls)
- Java-side `System.exit()` / `Process.killProcess()` via JNI, ending up in `android.os.Process.killProcess` — we'd need to hook via ART JNI callbacks
- A separate watchdog process sending SIGKILL to Uma (but then status would be signal 9, not clean exit 0, so this is unlikely)

### Open hypotheses on cause
H1. Uma's anti-tamper has its own integrity scanner over IL2CPP code that notices Hachimi's trampolines and calls a graceful-quit code path (likely `Application.Quit()` or similar Unity pattern that eventually routes through `ActivityManager.finishActivity` → `Binder` → exit without SIGABRT).
H2. Hachimi's `disable_gui: true` path leaves some hook site half-initialized and Unity managed code throws an unhandled exception that unwinds all the way out, leading Unity to exit cleanly.
H3. Something in Kitsune or ZygiskFrida interaction (both disabled/enabled variants tested; Kitsune enable-state did not change the outcome).

### Next experiments queued
1. Rebuild Hachimi without the IL2CPP hook loop (stub `hook::init`) — just confirm the Zygisk module loads silently. Distinguishes H1/H2 vs infra cause.
2. Hook Java `android.os.Process.killProcess()` and `java.lang.System.exit()` via Frida's `Java.perform` — catch the Java-side quit path.
3. Hook all `svc #0` instruction patterns in-process via Stalker or manual `MemoryRange.scan` for `0x010000d4` (svc #0 on arm64) in `libunity.so` and install `Memory.patchCode` breakpoints.
4. Bisect hooks: flip Hachimi source to install only a subset of IL2CPP hooks (e.g. skip `SafetyNet`/`IsIllegalUser`/`StoryChoiceController`) to identify which hook triggers the detection.

### Module / config layout right now
- `/data/adb/modules/hachimi/module.prop`: id=hachimi v0.24.0-edge-global versionCode=2400
- `/data/adb/modules/hachimi/zygisk/arm64-v8a.so`: built libhachimi.so, 15.8 MB
- `/data/adb/modules/hachimi/disable`: present (module will be inert on next boot until removed)
- `/sdcard/Android/media/com.cygames.umamusume/hachimi/config.json`: `{"disable_gui": true}`
- `/system/bin/.su_stash` → `./magisk` (su hidden)
- Kitsune `io.github.huskydg.magisk`: toggled to `disabled-user` via pm; no effect on outcome.

---

## Session 2026-04-18: Shield static analysis + per-thread tracer discovery

### What we learned
1. **HyperTech CrackProof payload = pure reporter, not a killer.** Unpacked shield (`lib__4e06__.so`, 0x17000 bytes) contains a detection writer and several readers, but no `exit`/`abort`/`kill` calls in the payload itself. The actual kill happens elsewhere (outside payload).
2. **Payload signature for runtime base discovery:** string `"checkLoadPath_extractNativeLibs_true"` at payload_base + **0x12205**. Unique, works across ASLR.
3. **Key offsets (Ghidra-confirmed):**
   - Writer (stores detection): +0x11910 — prologue `sub sp, sp, #0xc0` = `ff 03 03 d1`.
   - Reader (exposes flag): +0x118ec — `adrp+add` loads global buffer, reads flag at offset 0.
   - Buffer clearer: +0x16c90.
   - Writer-wrapper (thin call): +0x16c64.
   - Internal syscall wrapper (leaf, ends in `svc #0`): entry at +0x12118, `svc` at +0x12130. Calling convention: syscall number in **x7** (not x0), args in x0..x6, `mov x8, x7` before `svc`.
   - Detection buffer: `&DAT_6e54f740f0` — at payload_base + ~0x2f0f0, outside RX region (separate RW page).
4. **Per-thread ptrace tracers.** Every Uma thread (42 of them) has a dedicated ptrace tracer child. On our test run PID 9432 had 42 tasks, each with a distinct TracerPid (9499, 9509, 9510, ...). Implications:
   - Frida's `Process.setExceptionHandler` never fires on SIGSEGV — the tracers intercept signals first.
   - `crash_dump64` can't attach to produce a tombstone ("already traced by ...").
   - Per-thread tracer setup is what enforces anti-debug AND swallows our error-handling paths.
5. **The ~60s kill is SIGSEGV, not libc exit.** Logcat: `Zygote: Process <pid> exited due to signal 11 (Segmentation fault)`. None of our 10 libc hooks (`exit/_exit/_Exit/abort/raise/kill/tgkill/pthread_exit/pthread_kill/syscall`) fire.
6. **Frida Interceptor trampolines ARE the trigger, not the bypass patches.** Split test:
   - Patches + Interceptor hooks → Uma dies ~60s after script load.
   - Patches only (no Interceptor.attach anywhere) → Uma survives ≥ 200s.
   - Hypothesis: shield periodically scans code pages and detects modified first-bytes of hooked functions (libc or its own `svc` wrapper). Memory byte-patches on shield's *own* code (writer/reader) don't trip it — the shield apparently doesn't self-scan those ranges.

### Scripts written this session
- `scripts/frida_shield_bypass.py` — signature-scan locates shield, patches writer → `ret` and reader → `mov w0,#0; ret`.
- `scripts/frida_exit_backtrace2.py` — hooks libc + shield svc wrapper + installs exception handler (diagnostic; hooks are themselves the trigger so this mode is destructive).
- `scripts/frida_shield_verify.py` — read shield patch bytes via `Memory.scanSync` (fast).
- `scripts/frida_shield_combo.py` — unified bypass + trace + `--patches-only` mode that skips all Interceptor.attach hooks.
- `/tmp/examine_wrapper.py` — static disasm of unpacked shield @ +0x12100..+0x12200 to find svc wrapper entry.

### Pivot: we don't actually need Frida for Hachimi
Hachimi is a Zygisk module. It does its own injection at Zygote-fork time. The "~60s kill" work above is about Frida bypass, not Hachimi enablement. Re-focusing:

1. Disable ZygiskFrida (leaving it injected just adds a variable — and possibly triggers the same tracer-based detection).
2. Enable Hachimi alone, relaunch Uma.
3. Observe: does Uma stay alive? Does Hachimi's `INFO Hooking finished` appear? Does the translation overlay work?
4. If CrackProof still kills Uma with Hachimi alone, apply the shield bypass via **root `/proc/<pid>/mem` write** — no Frida, no trampolines, just the byte patches we already validated.

### Caveat re. "is Uma running?"
A live `pidof com.cygames.umamusume` is not sufficient evidence that Hachimi is working. Uma can be up but parked on the "not permitted to play" root-detection screen. Must take a screenshot via `scripts/screenshot.py` and visually verify we're past the root screen (title screen or home lobby). Also check logcat for Hachimi's `Hooking finished` line.
