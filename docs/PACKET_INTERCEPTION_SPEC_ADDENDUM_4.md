# Packet Interception Spec — Addendum 4: shield detection mechanism & hook-style pivot

**Status**: Extends Addendum 3. Live working notes from the 2026-04-18 session that pivoted from "just try C1" to a proper model of how the HyperTech CrackProof shield detects Frida. Written as results arrive.

**Date**: 2026-04-18

---

## TL;DR

The shield has **at least two distinct detection paths**, and they have different kill signatures:

| # | Trigger | Mechanism | Signature |
|---|---------|-----------|-----------|
| 1 | Frida thread names visible in `/proc/self/task/<tid>/comm` (`gum-js-loop`, `gmain`, `gdbus`, `frida-gadget`, `pool-frida`, …) | Thread-name string scan | SIGSEGV with `pc == fault_addr` (PAC-authenticated branch to corrupted pointer); tombstone produced |
| 2 | `Interceptor.attach` writing a 4-byte branch trampoline into `libil2cpp.so` `.text` | Code/checksum scan of libil2cpp | Clean `exit(0)`; **no tombstone**; logcat shows `Zygote: Process … exited cleanly (0)` |

Thread rename (#1) is bypassed. Path #2 blocks Branch C1 as implemented.

---

## What changed vs Addendum 3

Addendum 3 assumed the shield would either tolerate `frida-il2cpp-bridge` hooks or not — as one decision. Reality: the shield **tolerates Frida's presence (including its trampolines in Frida-owned memory) just fine, but actively scans libil2cpp for modifications**. It also independently scans thread names. The detections compose.

Addendum 3's decision matrix row *"If C1 kills instantly → network-layer pivot / Stalker-only"* still stands, but with more precision about why: it's not that "IL2CPP hooks are off the table" in the general sense — it's that **any hook style which writes bytes into libil2cpp `.text`** is off the table.

---

## Chronological findings (session log — kept for the next occupant)

### 1. Thread-name detection (path #1)

- First C1 probe (Hachimi disabled, ZygiskFrida only, no libc hooks in agent): Uma died 0.3-4s in.
- Tombstone_12: SIGSEGV SEGV_ACCERR on thread named literally `gum-js-loop`. PC == fault address (PAC corruption signature). Fault PC lives inside the 13.8MB anonymous r-x region (unpacked `lib__4e06__.so`) — same region as the earlier A1 crash.
- `Process.enumerateThreads()` does **not** list Frida's own threads (deliberate stealth in frida-gum), so the rename has to enumerate `/proc/self/task/` directly via `opendir`/`readdir` NativeFunction calls.
- Wrote `scripts/frida_rename_probe.py` and `scripts/frida_rename_hook_probe.py` as isolation tests; folded the working rename sweep into `scripts/frida_c1_probe.py` as an inline script that fires at t=0, 200ms, 1s, 3s post-attach.
- With rename in place, the agent survived ≥30s. `frida-il2cpp-bridge` discovery ran fine.

### 2. libil2cpp code-scan detection (path #2)

- With threads renamed, `discoverDeserializers()` (read-only reflection over `MessagePack.dll`) succeeded: enumerated 9 `Deserialize` overloads on `MessagePack.LZ4MessagePackSerializer` and 9 on `MessagePack.MessagePackSerializer`. **All share a single `virtualAddress`** (0x7a97617e84 this boot) because they're all `isGeneric: true, isInflated: false` — that VA is the IL2CPP generic dispatch stub, not the per-instantiation code.
- Added `hookGenericDispatchStub()` to `hook_deserializer.ts` + RPC export. This installs **one** `Interceptor.attach` on the shared VA with a plain onEnter/onLeave logger.
- Result: clean `exit(0)` 0.3s after the attach call. No tombstone. Logcat: `Zygote: Process 10806 exited cleanly (0)`. Same clean-exit signature that killed Hachimi in earlier sessions.
- Confirms the shield scans libil2cpp `.text` for modifications independently of Frida thread detection.

### 3. Stalker transform on shared generic dispatch VA — zero hits

- Implemented `probeStalkerTransformOnDispatch()`: follows every app thread with a transform callback that emits a `stalker_transform_hit` send whenever the recompiled block's start address matches the shared dispatch VA (0x7bb8261e84 this boot — VA differs per boot/ASLR).
- Uma survived 60+s under the transform, confirming it's as invisible to the libil2cpp scan as `addCallProbe` was. Good: the stealth property holds across Stalker APIs.
- Bad: **zero hits fired**, despite Uma progressing past login to the Account Link dialog — which means it had already exchanged multiple API round-trips and deserialized MessagePack responses. If the shared VA were a real call target, we'd have seen dozens of hits.
- Root cause: `Il2Cpp.Method.virtualAddress` on an un-inflated generic method returns a **metadata sentinel**, not a call target. All 9 `Deserialize` overloads on `LZ4MessagePackSerializer` share the same VA *and* all 9 on `MessagePackSerializer` share it too — that single VA is IL2CPP's template address for the metadata system. Real dispatch goes through per-instantiation method bodies resolved via `Il2CppMethodInfo.methodPointer` at first-use; those live at distinct VAs, one per concrete T.
- Consequence: Branch C1's "hook the Deserialize VA" premise is wrong. The shared VA is unhookable-by-design because nothing calls it. We need concrete per-instantiation VAs, or non-generic leaf methods that always get invoked on the decode path.

### 4. Pivot to non-generic method enumeration

- Added `findNonGenericMethodCandidates()` in `frida_agent/src/hook_deserializer.ts`: walks every class in the `MessagePack` image and emits a `catalog_method` send for each method with `isGeneric=false && virtualAddress != 0x0`. These are the leaf implementations (not templates), so their VAs are real call targets.
- Added RPC export in `frida_agent/src/agent.ts` and a `--catalog` flag in `scripts/frida_c1_probe.py` to drive it.
- Target hook candidates from the catalog:
  - `MessagePackBinary.Read*` — `ReadByte`, `ReadInt32`, `ReadBytes`, etc. (primitive readers, always non-generic, fire on every decode).
  - `LZ4MessagePackSerializer.LZ4Decode` and related entry-point helpers.
  - Per-type formatter `Deserialize` implementations — `Int32Formatter.Deserialize`, `StringFormatter.Deserialize`, and Cygames-specific `{Type}Formatter.Deserialize` (the actual leaves that fire per field).
- Next probe run: enumerate the catalog, pick the narrowest set that covers the decode path, Stalker-probe each.

### 4b. Follow-set narrowing (2026-04-18, later)

- Initial `Stalker.follow` on 139 threads made BlueStacks visibly sluggish. Stalker dynamically recompiles every basic block in every followed thread — whole-app follow (render, audio, GC, JNI, networking, all) is expensive on an emulator.
- Added `enumerateFollowTargets()` in `hook_deserializer.ts`: curated whitelist/skiplist keyed off `/proc/self/task/<tid>/comm`.
- First cut: 139 → 20 kept threads. Discovered Linux `/proc/<tid>/comm` is **15-char-capped** — `UnityChoreograph`, `IL2CPP Threadpool Worker`, `Background Job.Worker N` all truncate. Added exact matches for truncated forms: `UnityChoreograp`, `IL2CPP Threadpo`, `Background Job.` (prefix), `Loading.AsyncRe`, `Loading.Preload`. Second cut: 20 → 40 kept.
- Survival unaffected by narrowing (120s clean).

### 4c. Three non-generic VA probes — all zero hits

Three `Stalker.addCallProbe` installs on concrete VAs:
- `MessagePack.LZ4MessagePackSerializer.Decode(Byte[])` via live name lookup — 0 hits over 45s.
- `MessagePack.LZ4.LZ4Codec.Decode(Byte[], Int32, Int32, Byte[], Int32, Int32)` — 0 hits over 60s AND 120s (live tutorial), both 20- and 40-thread follow sets.
- `LZ4MessagePackSerializer` shared generic dispatch VA — already known zero-hit (metadata sentinel, see §3).

All three probes survived cleanly — shield is genuinely blind to Stalker. Problem is the **primitive**, not the stealth.

### 4d. Root cause: `addCallProbe` misses indirect dispatch

- ARM64 IL2CPP dispatch pattern is `LDR x8, [vtable_slot]; BLR x8` — the method pointer is loaded from a runtime metadata struct (`Il2CppMethodInfo.methodPointer`).
- `addCallProbe`'s register-equality check cannot resolve runtime-loaded pointers at probe-install time, so `BLR x8` flows past unobserved.
- Transform-mode Stalker (`Stalker.follow(tid, { transform })`) checks `iterator.next().address.equals(targetVa)` at every block boundary, catching the jump regardless of how control reached it. This is the correct primitive for indirectly-dispatched IL2CPP methods.

### 4e. VA ASLR handling confirmed working

Catalog-run VAs (`0x7bbab6a0a4`, `0x7bbab96614`) shifted by ~0x20000000 at next boot (`0x7bb9ac40a4`, `0x7bba3c7614`). Live name lookup via `Il2Cpp.perform` → domain → assembly → image → class → method is the durable resolution path.

### 4f. Next planned experiment

Clone `probeStalkerTransformOnDispatch` into `probeStalkerTransformOnLz4Codec` targeting the live `LZ4Codec.Decode` VA. Unlike the earlier transform-on-shared-dispatch experiment (0 hits because the shared VA is a metadata sentinel, not executable code), `LZ4Codec.Decode` is a concrete non-generic method with real code — transform mode should fire on the `BLR` target match.

### 4h. Traffic generator + transform probe on `MessagePackBinary.ReadBytes` — zero hits (2026-04-18, later)

- Built `scripts/frida_traffic_gen.py`: thin wrapper that loops `.venv/bin/python scripts/run_one.py` (real Uma bot, not a throwaway OCR tapper) and handles SIGTERM cleanly. Spawned as a background subprocess from `frida_c1_probe.py` for the observation window; SIGTERMed on probe exit. 5 full career turns completed at ~28s mean during the 120s window — confirms real API traffic was flowing.
- Implemented `probeStalkerTransformOnMpReadBytes` targeting the non-generic `MessagePackBinary.ReadBytes(Byte[], Int32, Int32&)` at live VA `0x7bba39d45c`. `ReadBytes` is too large to inline and sits on nearly every msgpack decode path — decisive sanity check.
- Result: **0 hits over 120s / 5 career turns**. Transform installed cleanly, 39 IL2CPP-relevant threads followed for the full window, agent survived.
- Diagnosis: Stalker transform primitive is not reaching IL2CPP method VAs on this build. Zero hits on a method this central rules out AOT inlining (which could have explained `LZ4Codec.Decode`) and rules out `addCallProbe`-vs-`BLR xN` mismatch. Two candidate causes:
  (a) `frida-il2cpp-bridge`'s `Method.virtualAddress` points into a metadata stub / indirect thunk, not the first instruction actually executed at call time.
  (b) The threads executing managed code are not among our 39 followed threads — the 100+ skipped threads may be the hot path even after expanding the whitelist to IL2CPP-branded names.
- Either way, continuing to hunt for "the right IL2CPP VA" is no longer productive. Five targets tried (generic dispatch, `LZ4MessagePackSerializer.Decode`, `LZ4Codec.Decode` via both `addCallProbe` and transform, `MessagePackBinary.ReadBytes` via transform) — all zero-hit.

### 4j. SSL-layer probe survived — first successful Interceptor.attach this project (2026-04-18, later)

- **Major finding**: `Interceptor.attach` on `libjavacrypto.so!SSL_read` **survived** a clean 90s probe window with traffic-gen running. No shield kill, no SIGSEGV, no `exit(0)`. First successful `Interceptor.attach` on any module in this project.
- Confirms path #2 (code-scan kill) is scoped specifically to libil2cpp `.text`, **not** a generalized "any Interceptor trampoline" detector. Earlier belief that libc hooks tripped the shield may have been correct for libc specifically, but libssl / libjavacrypto / libcrypto appear to be outside the scanned set.
- Phase A module enumeration (survived 20s on its own):
  - `/system/lib64/libssl.so` — BoringSSL, 376 KB, mangled `bssl::` symbols (`dtls1_*`, `tls1_*`).
  - `/apex/com.android.conscrypt/lib64/libjavacrypto.so` — Conscrypt JNI shim, 311 KB. Has `NativeCrypto_SSL_read`, `NativeCrypto_SSL_write`, `NativeCrypto_BIO_read`, `NativeCrypto_ENGINE_SSL_read_direct`, and exports plain `SSL_read` as a JNI wrapper.
  - `libcrypto.so` (3 mappings) — BoringSSL crypto primitives.
  - `libwebviewchromium.so` + 2 helpers — 99 MB, no SSL-matching symbols.
  - No standalone boringssl/cronet modules in Uma's libs (Cygames doesn't bundle its own).
- **Hook-resolution footgun**: `Module.getGlobalExportByName("SSL_read")` returned the **Conscrypt** `SSL_read` at `libjavacrypto.so+0x...`, NOT `libssl.so`'s BoringSSL `SSL_read`. Must do module-local lookup instead.
- Payload capture on Conscrypt `SSL_read`: only 2 `retval>0` events over 90s / 34 career turns:
  - 644 bytes: `HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=UTF-8\r\n...` (cleartext HTTP/1.1 header, chunked JSON response).
  - 5 bytes: `0\r\n\r\n` (chunked terminator).
- Traffic is **not** the Uma game API. Most likely Google Play services / Firebase / analytics / hachimi mock going through Conscrypt JNI. Cygames game traffic uses OkHttp + BoringSSL directly, which flows through `libssl.so!SSL_read`, not the Conscrypt wrapper.
- **Next step**: hook `libssl.so!SSL_read` specifically via module-local `libssl.so.findExportByName("SSL_read")` (and `SSL_write` for request plaintext). If survival holds, expect HTTP/2 framing with msgpack/LZ4 game-API responses. If `SSL_read` isn't an export in libssl (BoringSSL builds may strip it — all exports seen were `bssl::` mangled), use `enumerateSymbols` to find the mangled BoringSSL read/write, likely `SSL_read_internal` or similar. Fallback: hook Conscrypt's `NativeCrypto_ENGINE_SSL_read_direct`, which wraps the libssl call.

### 4i. Strategic pivot — native SSL layer

Next attempt: hook `SSL_read` (and optionally `SSL_write`) in libssl / boringssl via plain `Interceptor.attach`. Justification:
- Path #2 (libil2cpp code-scan kill) has only been observed against `libil2cpp.so` `.text`. libssl / libc / other natives haven't been seen to trip it — but also haven't been tested, so first probe must be minimal read-only onEnter logger to confirm survival.
- TLS plaintext sits upstream of all msgpack work (framing, LZ4, deserialize). One interception point catches everything.
- msgpack parsing happens host-side in Python via the `msgpack` package — no IL2CPP needed at all.
- `Interceptor.attach` on libssl, if survivable, is a drastically simpler tooling story than Stalker gymnastics.

**Next experiment**: `scripts/frida_c1_probe.py --ssl-probe` that (a) enumerates loaded modules matching `/ssl|boringssl|tls|crypto/`, (b) locates `SSL_read` and similar exports, (c) installs minimal Interceptor.attach with onEnter buffer-ptr + size logging, (d) runs with traffic-gen alongside, (e) reports survival + hit count + first N bytes of arg buffer.

### 4g. Session state for next occupant

User stuck at title screen in the 45s window, then past tutorial, then active career gameplay by session end. Live `--no-launch` attach against the running Uma session is the right mode for probing during active gameplay (avoids force-stop + monkey-launch restart).

### 4k. Probe harness attaching to wrong pid in `--no-launch` mode (2026-04-18, later)

- **Major diagnostic finding.** `frida_c1_probe.py` polls `pidof` for Uma, filters by exact `/proc/<pid>/cmdline` match against `com.cygames.umamusume`, picks that as "main pid". Then opens the gadget device and tries to find a matching process. If the gadget doesn't list the main pid, the script silently falls back to "first available gadget process" — a bystander helper (Uma runs multi-process).
- Most recent Conscrypt-engine probe run exposed it: main=`23277`, gadget offered only `[28814]`. Script attached to 28814. libil2cpp and libjavacrypto were loaded there (APEX shared-lib), so symbol resolution and hook install all succeeded, but the OkHttp client doing game-API TLS lived in 23277 → zero hits.
- **Retroactively may explain prior zero-hit probes**: all 5 Stalker probes (generic dispatch, Lz4Decode, LZ4Codec via addCallProbe, LZ4Codec via transform, MessagePackBinary.ReadBytes via transform). Each resolved IL2CPP symbols cleanly and survived, caught nothing. If the gadget had drifted off the main pid by the time we ran them, that alone explains the zeros — no need to invoke AOT inlining, transform-primitive bugs, or bridge VA-sentinel theories. The addCallProbe-vs-BLR theory (§4d) may still be correct but was never the *necessary* explanation.
- **What DID work retroactively**: the early C1 `hookGenericDispatchStub` probe that killed Uma in 300ms WAS in the main process — Uma was cold-launched and the shield's exit(0) triggered. The Conscrypt `SSL_read` probe that captured Google Play HTTP/1.1 JSON was ALSO in *some* process with active JNI TLS traffic — but likely a bystander doing only analytics, which explains why we saw bytes but never real game API.
- **Fix plan**:
  - Harness must refuse to attach to the fallback pid. If gadget list ≠ main pid, force-stop + relaunch to cycle ZygiskFrida into the fresh process, retry. Bail non-zero on second miss.
  - Post-attach sentinel RPC (`ping`) that reports `Process.findModuleByName("libil2cpp.so") !== null` AND confirms process cmdline. Fail fast if either missing.
  - Widen cold-launch wait; ZygiskFrida may need more time to inject. 8s may be too short for some ASLR paths.
- **Open question**: which prior zero-hit probes would have succeeded against the correct pid? Re-running Stalker-transform-on-`MessagePackBinary.ReadBytes` with verified-main-pid attachment should tell us whether transform actually works on IL2CPP methods, or whether the bridge-VA-sentinel theory was correct.

### 5. Implications for the Addendum 3 plan

- Branch C1 as specified (hook Deserialize via `m.implementation = fn` or equivalent `Interceptor.attach`) is blocked. Both write trampolines into libil2cpp `.text`.
- Branch A4 (neutralize the scanner in `lib__4e06__.so`) is now the single most valuable upstream fix — every hooking strategy downstream of it becomes viable.
- Intermediate options (below) may avoid the scanner without touching libil2cpp.

---

### 4m. Hypothesis B confirmed — Uma uses statically-linked TLS inside libil2cpp (2026-04-20)

Two clean probes discriminated A (traffic-gen failure) from B (wrong TLS provider):

**`--fixed-ssl` (dynlink-hash path, 100s cold-launch watch).**
- `SSL_read`, `SSL_write`, `SSL_read_ex`, `SSL_write_ex`, `SSL_do_handshake`: **all missing** from `Module.getGlobalExportByName`.
- `BIO_read`, `BIO_write`: resolved at `0x7f23f2c7dc` and `0x7f23f2c924`, hooked successfully, **zero hits** over the full window.
- Uma reached the Account Link modal during this watch (confirmed by screenshot) — login API traffic happened.
- Probe survived the full duration; clean detach, `died_early=False`.

**`--wide-ssl` (allow-list `enumerateSymbols`, 40+s of watch so far, probe still running).**
- Scanned: libandroid_net.so, libcrypto.so (×3 maps), libjavacrypto.so, libssl.so, libwebviewchromium_loader.so, libwebviewchromium.so.
- Correctly skipped: lib__4e06__.so, libmain.so, libunity.so, libil2cpp.so, libnative.so.
- 12 SSL symbols found (hidden-visibility + exported), 12 hooks installed cleanly:
  - libjavacrypto.so × 6 (Conscrypt: `NativeCrypto_SSL_read`, `NativeCrypto_SSL_write`, `NativeCrypto_ENGINE_SSL_read_direct`, `NativeCrypto_ENGINE_SSL_write_direct`, `..._read_BIO_direct`, `..._write_BIO_direct`)
  - libssl.so × 6 (BoringSSL: `ssl_read(bio_st*,char*,int)`, `ssl_write(bio_st*,const char*,int)`, `ssl_read_impl(ssl_st*)`, `ssl_read_buffer_extend_to`, `ssl_write_buffer_flush`, `ssl_write_client_hello_without_extensions`)
- **Zero hits on any of the 12 symbols across 40+s including login flow.** Uma surviving cleanly.

**Conclusion:** Uma Global's HTTPS stack bypasses every globally-visible TLS library. Nothing in `libssl.so` (system BoringSSL), `libjavacrypto.so` (Conscrypt), `libcrypto.so` (OpenSSL primitives), or `libwebviewchromium.so` (Chromium net) sees any traffic. TLS must be **statically linked inside `libil2cpp.so` (Unity/Mono/Cygames C# stack)** — the only large module we can't enumerate without tripping the PROT_NONE guard (Shield Path #3).

**Implications for next work:**
- Branch C1 via platform TLS is dead. Stop chasing SSL_read hooks outside libil2cpp.
- The data path must be tapped either (a) inside libil2cpp via **Stalker transform** on statically-linked BoringSSL or the Cygames HTTP wrapper, requiring offline Ghidra analysis to find the offsets, or (b) at the post-decrypt boundary (MessagePack.Deserialize) which we already confirmed returns zero hits via transform — so either the bridge's VA is wrong or the thread filter is wrong, and that needs debugging.
- Option (c): hook at the **socket layer** (`recvfrom`/`recv`/`sendto`) — we'd capture encrypted bytes but at least confirm the fd and message boundaries; then correlate with a post-decrypt hook once we find one.

---

### 4l. Shield Path #3 — wide-module `enumerateSymbols` trips PROT_NONE guard (2026-04-19, autonomous)

**What we ran:** cold-launch probe with sentinel-verified pid, thread rename, then one call to `scanAllSslSymbols()` — a version that iterated **every** loaded module's symbol table looking for `SSL_read|SSL_write|BIO_*|ssl3_*|bssl*` matches.

**Result:** Uma died at t=10.7s, ~2.7s after `scanAllSslSymbols()` was invoked. Logcat:

```
F libc : Fatal signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0xf9229a3de0
        in tid 27863 (Thread-JVM-96), pid 27454 (games.umamusume)
I Zygote: Process 27454 exited due to signal 11 (Segmentation fault)
```

`Thread-JVM-96` is our renamed `gum-js-loop`. Same signature class as the original A1 finding: SIGSEGV in the JS thread while it iterates shield-mapped memory.

**Implication:** enumerating symbols across `libil2cpp.so` and `libnative.so` is fatal by itself, independent of any `Interceptor.attach`. The shield plants page-level tripwires over the symbol-table regions of those modules; a gum-js-loop `enumerateSymbols()` walk reads a guarded page and segfaults the thread.

**Bypass (applied):** `collectSslSymbols()` now uses two regexes — `SYM_ENUM_SKIP_RE` (never touch libil2cpp/libnative/libunity/libmain/`lib__xxxx__.so`/anonymous) and `SYM_ENUM_ALLOW_RE` (only walk modules matching `/ssl|tls|crypt|boring|cronet|net\.so|chromium|conscrypt|openssl|quic|curl/i`). Emits `ssl_sym_enum_modules` telemetry so we can confirm the filter.

**Safer alternative added (`installFixedSslHooks`):** resolves `SSL_read`/`SSL_write`/`SSL_read_ex`/`SSL_write_ex`/`BIO_read`/`BIO_write`/`SSL_do_handshake` via `Module.getGlobalExportByName` (dynamic-linker hash path, identical to what ART/libc do thousands of times per second — shield cannot flag it). RPC: `installFixedSslHooks()`, probe flag: `--fixed-ssl`.

**Known shield detection paths as of 2026-04-19:**
1. Frida thread names → PAC-corruption SIGSEGV. Bypass: `/proc/self/task/<tid>/comm` rename.
2. libil2cpp `.text` modification (Interceptor trampoline) → clean `exit(0)` ~0.3s. Bypass: Stalker transform only; never `Interceptor.attach` on libil2cpp.
3. Wide `enumerateSymbols` walk over shield/packer modules → SIGSEGV SEGV_MAPERR in JS thread. Bypass: allow-list enumeration OR fixed-name `findGlobalExportByName` only.

---

## Next-step option tree (current working menu)

### Option 1: Stalker / Il2Cpp.trace — **SUCCESS** ✅

**Result**: Stalker bypasses the libil2cpp code-scan detector. Two stages tested:

1. `probeStalkerFollow()` — Stalker.follow on 143 application threads (enumerated via `/proc/self/task`, excluded self + renamed-from-Frida threads). Uma survived 25+ seconds, ended via clean `session.detach()` from the host side.

2. `probeStalkerOnGenericDispatch()` — same follow + `Stalker.addCallProbe(sharedGenericDispatchVA, cb)`. Uma survived 45+ seconds.

Both confirm: **Stalker's dynamic recompilation is invisible to the shield's detector.** Interceptor.attach writes a BR into libil2cpp `.text` → detected in ~300ms. Stalker runs the recompiled copy from a scratch buffer → undetected.

Notes / observations:
- `Process.enumerateThreads()` returns 0 threads under ZygiskFrida/gadget — we enumerate `/proc/self/task/` directly via opendir/readdir NativeFunction, same pattern we used for the thread rename.
- No `stalker_call_hit` events fired during the 45s probe window. Reason: Uma was stuck at the "You are not permitted to play on this device." root check (see *root-hide regression* below), so it never made any API calls. When Uma is past the device gate and actually hits the network, these should fire.
- The fact that `Stalker.addCallProbe` fires on the shared generic dispatch VA is still unverified. It is possible that IL2CPP-inflated generic callsites route around the shared stub (calling per-instantiation thunks directly). If so, we'd need to find the inflations via GC or metadata walking.

**Next**: re-enable Uma's device-check bypass (stash/rename su + disable Kitsune package), boot into the game, confirm `stalker_call_hit` fires on real network responses. If it doesn't fire, enumerate inflated Deserialize methods and hook each of them with Stalker probes.

### Root-hide regression encountered this session

Hit "You are not permitted to play on this device." — Uma detects Kitsune's su/magisk binaries. Attempted mount-bind of `/data/local/tmp/empty` over `/system/bin/magisk` to break the `/system/bin/su → ./magisk` symlink. This also broke `/sbin/magisk64` (shared inode or common bind source on BlueStacks), which killed `su` itself. BlueStacks needs reboot to restore; then the bypass must be applied atomically (single su session: create empty + bind su + bind magisk + pm disable io.github.huskydg.magisk).

Package to disable: `io.github.huskydg.magisk` (Kitsune fork of Magisk).

### Option 2: Move interception off libil2cpp entirely

Hook a lower layer that is not in libil2cpp `.text`:
- **BoringSSL `SSL_read` / `SSL_write`** in libboringssl.so / libssl.so — we get TLS cleartext before/after MessagePack framing. Shield hasn't been observed scanning libssl.
- **libcurl / libnative's HTTP client** if one is present — similar.
- **JNI/libart boundary** where managed code calls into native HTTP — also off libil2cpp.

Tradeoff: we re-inherit the msgpack decode problem the IL2CPP pivot was designed to escape. We'd have to ship our own msgpack parser host-side (straightforward in Python — `msgpack-python` exists) or re-call the game's Deserialize via an out-of-process channel (complicated).

### Option 3: A4 unpack-time scanner neutralization

Stalker-trace the 13.8MB unpacked region during shield init (before the detector starts). Identify the scan function by its signature (reads libil2cpp .text in large strides, computes a hash/checksum, compares against a stored value, calls `exit` on mismatch). NOP the compare or the call site. This is the durable fix — if we can do it once, future hooks are free.

Biggest upfront investment. Defer unless 1 and 2 both fail.

### Option 4 (speculative): hook the *caller* of Deserialize, not Deserialize itself

If the caller lives in a Cygames-compiled assembly rather than `MessagePack.dll`, it's still libil2cpp code, so this doesn't dodge path #2 — ruled out.

Unless the caller is in libart (i.e., the method is invoked via reflection from a JNI path), in which case this becomes equivalent to Option 2.

---

## Files touched this session

- `frida_agent/src/agent.ts` — disabled startup `installPtraceBypass()` and `installDlopenWatcher()` (both tripped path #2 or a libc-watcher variant); added RPC-only exports for each; added `hookGenericDispatchStub` export.
- `frida_agent/src/hook_deserializer.ts` — added `hookGenericDispatchStub()` (litmus-test hook on the shared generic dispatch VA). Added `findNonGenericMethodCandidates()` (walks `MessagePack` image, emits `catalog_method` sends for every non-generic method with a real VA). Added `enumerateFollowTargets()` (curated whitelist/skiplist keyed off `/proc/self/task/<tid>/comm`, handles 15-char truncation). Added `probeStalkerOnLz4Decode` (addCallProbe on `LZ4MessagePackSerializer.Decode`) and `probeStalkerOnLz4Codec` (addCallProbe on `LZ4Codec.Decode`). Existing `discoverDeserializers()` and `installDeserializerHooks()` unchanged.
- `scripts/frida_c1_probe.py` — added `--stalker-lz4`, `--stalker-lz4codec`, and `--stalker-transform-readbytes` flags to drive the new probes. Spawns `scripts/frida_traffic_gen.py` as background subprocess for the observation window and SIGTERMs it on exit.
- `frida_agent/src/hook_deserializer.ts` — added `probeStalkerTransformOnMpReadBytes` (transform-mode follow targeting concrete non-generic `MessagePackBinary.ReadBytes` VA).
- `scripts/frida_traffic_gen.py` — new; thin loop around `.venv/bin/python scripts/run_one.py` with clean SIGTERM handling, used to generate real API traffic during probe windows.
- `frida_agent/src/rename_threads.ts` — created (superseded by inline rename in the probe script; kept for reuse).
- `scripts/frida_rename_probe.py` — minimal standalone rename experiment.
- `scripts/frida_rename_hook_probe.py` — 3-mode (inert / hook_only / rename_and_hook) isolation test.
- `scripts/frida_c1_probe.py` — main C1 harness: launch + pid-poll + gadget wait + inline `/proc`-enumeration rename + RPC discover/stub-hook/full-hook. Added `--catalog` flag to drive `findNonGenericMethodCandidates()`. Added `--ssl-enum` and `--ssl-probe` flags.
- `frida_agent/src/hook_ssl.ts` — new; `enumerateSslModules()` (module/export inventory with regex match against `/ssl|boringssl|tls|crypto/`) and `installSslReadProbe()` (minimal onEnter buffer-ptr + size + onLeave retval capture, first N bytes).

---

## Open questions for the next session

1. Does the scanner trigger on **any** page in libil2cpp `.text`, or only on specific hot regions? (Stalker follow-call may answer this indirectly.)
2. What is the scan cadence? 0.3s to detection suggests a busy loop in a shield thread; we could potentially starve it by load-spiking the scheduler, but that's fragile.
3. Is the scanner a single code page we can identify and NOP (A4), or is it replicated / checksummed itself?
4. Does BoringSSL live in a separate module the shield doesn't scan, on Uma Global?
5. Does the generic metadata VA ever get called for anything (debug path, reflection, fallback), or is it pure metadata that never executes?
6. ~~Does transform-based probing on a concrete (non-sentinel) VA actually catch indirect BLR in practice, or does IL2CPP inline simple wrappers like `LZ4Codec.Decode` such that the original VA is never entered at all?~~ **ANSWERED — NO.** Transform on concrete `MessagePackBinary.ReadBytes` VA fired 0 times over 5 real career turns (§4h). Method too large to inline, so the issue is not inlining; either `frida-il2cpp-bridge`'s `Method.virtualAddress` isn't the runtime call target on this build, or the hot-path threads aren't in our follow set.
7. ~~Does libssl / boringssl live in a module the shield does NOT scan for Interceptor trampolines?~~ **ANSWERED — NO (for Conscrypt at least).** `Interceptor.attach` on `libjavacrypto.so!SSL_read` survived a clean 90s probe with traffic-gen running (§4j). `libssl.so` proper not yet tested but expected same.

---

## Addendum: how to continue from here

If you (future-session) pick this up: read `project_shield_kill_path.md` in memory first. It has the minimum context. Then this addendum's Option 1 is the cheapest next experiment; the probe harness is ready, the only work is adding a Stalker-based RPC export to the agent.

---

## 2026-04-21 session update: IL2CPP managed-layer dead end, LZ4 shortcut, shield path #3

### What was tried and what it cost

Extending Option 3 from the previous session ("broad scan"), we iterated through every IL2CPP hook candidate we could spot in the dumps — all dead code at runtime:

| Candidate | Outcome |
|---|---|
| `Gallop.HttpHelper.CompressRequest` / `DecompressResponse` (Stalker transform) | 0 hits / 140s live HTTP |
| `Gallop.CryptAES.EncryptRJ256` / `DecryptRJ256` (Stalker transform) | 0 hits / 90s live HTTP |
| ~397 `Gallop.*Task.Deserialize(byte[])` across all `umamusume.Http` Task subclasses (Stalker transform AND Interceptor.attach) | 0 hits / 4+ min live HTTP across career home + Skills + Races taps. Confirmed NOT a Stalker-gating bug — Interceptor.attach on 200 resolved VAs with attachOk=200/attachErr=0 produced 0 hits. The methods are compiled-in but the runtime doesn't dispatch through them in this build. |

Conclusion: the entire IL2CPP-managed HTTP layer in the dumps is stale/unused in the shipping binary. Stop chasing IL2CPP method-name hook targets.

### Why: Uma bundles its own TLS stack

Follow-up SSL reconnaissance via `--ssl-enum`, `--conscrypt-engine`, `--wide-ssl`, `--fixed-ssl`:

| Hook layer | Hits in 30s live HTTP |
|---|---|
| `libssl.so` BoringSSL (system, `bssl::*` mangled C++) | 0 |
| `libjavacrypto.so` Conscrypt `NativeCrypto_SSL_{read,write}` | 0 |
| `libjavacrypto.so` Conscrypt `NativeCrypto_ENGINE_SSL_{read,write}_direct` | 0 |
| `libcrypto.so` `BIO_read` / `BIO_write` | 0 |
| `SSL_read` / `SSL_write` via `findGlobalExportByName` | symbol missing (statically linked somewhere) |

`strings` on the pulled `/data/app/.../lib/arm64/libnative.so` (2.1MB, stripped — `nm` returns "no symbols") found the actual TLS stack:
- `CLIENT libcurl 7.73.0`
- `mbedTLS: ssl_init failed`, `mbedtls_ssl_get_session returned -0x%x`, `ssl_handshake returned - mbedTLS: (-0x%04X) %s`
- `nghttp2` HTTP/2 helpers

**Uma's HTTP stack is statically-linked libcurl 7.73.0 + mbedTLS inside libnative.so.** No Android system TLS library is used for game API traffic. The only exported symbols that survive the strip are the four `LZ4_*` functions (see next section). Memory: `project_uma_tls_stack.md`.

### The LZ4 shortcut (CarrotJuicer pattern)

A quick research pass on existing Uma tooling turned up the definitive answer: **CarrotJuicer** (Windows) and **Riru-CarrotJuicer** (Android, 2020-2022) have been intercepting Uma's packets by hooking the two LZ4 exports in libnative.so:

- `LZ4_decompress_safe_ext` → response side, plaintext msgpack in the `dst` buffer on retval > 0
- `LZ4_compress_default_ext` → request side, plaintext msgpack in the `src` buffer at entry

Wire format is `TLS(LZ4(msgpack))`, so the LZ4 layer is the cleanest plaintext boundary in the whole stack — and it's exposed as four real exports. No Ghidra xref hunt needed. friTap (pattern-matching mbedtls hooker) and Karta (open-source FLIRT-style matcher) are still-meaningful fallbacks if the LZ4 path is blocked, but they're second-tier.

Hachimi (2024-current Uma translation mod) deliberately does NOT hook libnative.so — its `LibNative_Runtime` Rust module only hooks SQLite3 wrappers. Karta source on its own il2cpp side. That avoidance is informative.

### Shield path #3: libnative.so prologue integrity check

`installLz4Hook()` (Interceptor.attach on `libnative.so!LZ4_decompress_safe_ext` at libnative base + 0x1ca86c) installed cleanly and the agent emitted the `lz4_hook installed` event. **1.5 seconds later Uma SIGSEGV'd — `SESSION DETACHED ('connection-terminated')` — with zero `lz4_call` events captured.** Same kill-with-trampoline-present pattern as path #2, but a different scan target: this one hashes libnative.so (not libil2cpp.so).

Adding to the detection table at the top:

| # | Trigger | Mechanism | Signature |
|---|---------|-----------|-----------|
| 3 | `Interceptor.attach` writing a branch trampoline into `libnative.so` `.text` at an exported-symbol entry point | Code/checksum scan of libnative exports (likely the four `LZ4_*`) | SESSION DETACHED 'connection-terminated' ~1.5s after attach; no tombstone filed during 2026-04-21 run |

So the shield scans **at least** libil2cpp (path #2) and libnative (path #3). `libssl.so` / `libjavacrypto.so` are in the ignore set (path #2 testing confirmed these survive 90s attaches). This is consistent with the shield caring about game-proprietary binaries and ignoring the Android system TLS stack (because Uma doesn't use it anyway).

### Current thinking — next-step ranking

Our target remains fixed (the two LZ4 exports in libnative.so). The open question is **which hook technique slips past path #3**. Three approaches ranked by cost to prove/disprove:

**1. Hook at `LZ4_decompress_safe_ext + 0x20` (or +0x40) — 5 minutes, high upside.**
Most lightweight integrity checks hash only the first 16–32 bytes of a function (the prologue). Shifting Interceptor.attach past the prologue bytes means the trampoline goes into a later instruction; if the shield doesn't walk the whole function, we bypass the hash while still intercepting every call. Code change: one line in `frida_agent/src/hook_lz4.ts` to `target.add(0x20)`. Fast fail: Uma still dies in ~1.5s means the hash window is larger. Fast win: hits start flowing.

**2. Stalker-based LZ4 trace — 30–60 minutes, medium upside.**
Stalker never patches the original function — it recompiles basic blocks into a private JIT buffer and redirects execution through branch patching in thread contexts. The shield's byte-hash of `LZ4_decompress_safe_ext` remains intact. Prior work in this codebase already demonstrated Stalker is invisible to path #2 (libil2cpp scan). Cost: must follow every HTTP thread, which means enumerating which threads actually call `LZ4_decompress_safe_ext` (probably OkHttp worker pool + UnityMain + Job.Worker* threads). Agent already has Stalker plumbing — add a new RPC `probeStalkerOnNativeLz4` that follows all non-framework threads with a transform emitting on block start == libnative base + 0x1ca86c.

**3. Zygisk/Dobby port — day-scale, proven answer.**
Rewrite as a Zygisk module that hooks libnative.so at load time, before the shield initializes. This is the Riru-CarrotJuicer approach. Dobby inline-hooks are conceptually similar to what Frida does, but they happen *before* the shield's integrity baseline is established, so the baseline already captures the patched bytes. Significant new C++/NDK code. Only do this if (1) and (2) both fail.

**Out of scope (but tracked):**
- Option D (kill the shield first) — high effort, high risk, not aligned with current discovery velocity.
- Hardware breakpoints on arm64 — 4 slots, sufficient for LZ4_compress + LZ4_decompress, but Frida's HW BP support is experimental and may have its own detectable signatures.

### Files touched this session

- `frida_agent/src/hook_deserializer.ts` — added `interceptAttachOnTaskDeserialize(maxAttach)` for the Stalker-vs-Interceptor sanity check; confirmed Task.Deserialize is dead.
- `frida_agent/src/agent.ts` — new RPC export `interceptAttachOnTaskDeserialize`.
- `scripts/frida_c1_probe.py` — added `--task-deserialize-intercept` and `--lz4-native` flags; both wired to bypass the IL2CPP discovery path.
- `.claude/.../memory/project_uma_http_layout.md` — rewritten with the invalidation result.
- `.claude/.../memory/project_uma_tls_stack.md` — new, documents the libcurl+mbedTLS-in-libnative.so discovery and the LZ4 shortcut.

Next concrete commit: apply the +0x20 offset shift in `hook_lz4.ts` and rerun `--lz4-native`. If Uma survives past 10s, the plaintext msgpack stream starts showing up in `lz4_call` events.
