# Packet Interception Spec — Addendum 2: Shield-dissection dead end, IL2CPP pivot

**Status**: Supersedes the WS-4 LZ4 hook in Addendum 1. Does not supersede WS-1, WS-2 (Frida infra still stands). Does not touch WS-3 / WS-5 / WS-6 / WS-7 (host-side pipeline unchanged).

**Date**: 2026-04-18

---

## Context: why Addendum 1's WS-4 is dead

Addendum 1 pivoted from the Hachimi Edge APK-patching path to **frida-server + Frida agent + `Interceptor.attach` on `LZ4_decompress_safe_ext` in `libnative.so`**. That assumption survived WS-1 (frida-server attaches on BlueStacks + Uma Global) and WS-2 (agent scaffold loads). It did not survive WS-4.

### What WS-4 tried

1. Located `LZ4_decompress_safe_ext` at `libnative.so + 0x??` via `Module.findExportByName`.
2. Installed `Interceptor.attach` on it from the Frida agent.
3. Idle-attached (no hooks) → Uma survived 95 s+.
4. With LZ4 hook active → Uma `SIGSEGV'd within 1–2 s` inside an anonymous IL2CPP JIT region. The crash address moved between runs — classic anti-RE "corrupt a pointer rather than call abort()" pattern.

### Why it failed

Uma's anti-tamper shield periodically scans `libnative.so`'s `.text` pages. When it sees our trampoline's first bytes (branch to Frida's hook gadget) it does **not** call `exit`/`abort`. It deliberately writes a bad pointer or jumps to junk memory, producing an apparent "random" crash far from the real trigger. Every libc exit trap (`exit/_exit/_Exit/abort/pthread_exit/syscall/kill/tgkill/tkill/raise`) was hooked — zero fired. Per-thread `ptrace` tracers (42 of them on Uma Global) also swallow signal handlers before Frida's `Process.setExceptionHandler` can see them.

**Bottom line:** any byte modification inside `libnative.so`'s executable pages is detected. LZ4 hook is dead.

### Parallel attempt: Hachimi Zygisk (full IL2CPP suite)

Built `libhachimi.so` from Hachimi-Edge with the JP-only region gate patched to allow Global. Packaged as `/data/adb/modules/hachimi/zygisk/arm64-v8a.so`. Module loads, Hachimi logs `Game region: Global` → `Got il2cpp handle` → full IL2CPP hook suite → `Hooking finished`. **~2 s after** `Hooking finished`, Uma `exited cleanly (0)` via a path libc exit traps do not see.

Hachimi's hooks are installed via il2cpp method replacement, not libnative trampolines. So this death is a *different* check — most likely an IL2CPP-level integrity scan inside Unity that watches `GameAssembly.so` (libil2cpp) vtables or method pointers.

---

## What this session (2026-04-18) ruled out

The session's question was: *is the HyperTech CrackProof payload (`lib__4e06__.so`-unpacked 0x17000 rwxp region) the thing calling exit?* The answer is **no** — twice over.

### Finding 1: the detection flag buffer is never written

Decoded the shield's "reader" (`+0x118ec`) and "writer" (`+0x11910`) via capstone. Both operate on a struct at `shield_base + 0x2f0f0` (page `+0x2f000`, offset `+0xf0`). The struct lives in a *second* anon mapping — `rw-p`, 0x19000 bytes — directly above the `rwxp` code region. Prior `/proc/maps` filters missed it because they only inspected `rwxp` entries.

Polled 256 bytes at `shield_base + 0x2f0f0` every 10 ms for 20 s across Uma's full lifetime. The buffer stays **all-zero** for the entire run. Uma still dies. → The writer never fires; the reader's return value is always 0. Shield's own bookkeeping buffer is not on Uma's kill path.

### Finding 2: the shield's generic `svc` wrapper is not on the kill path

Scanned `/tmp/shield_live.bin` for `svc #0` (`d4000001`). Exactly **one** occurrence, at `shield_base + 0x12130`, inside a generic syscall leaf (`mov x8, x7; svc #0; ...`, entry at `+0x12118`). Patched the `svc` to `nop` via `/proc/<pid>/mem`. Uma still exited at the usual ~4 s post-launch mark, `dt_from_patch = 4.2 s`. → Shield doesn't call `exit_group` through its own syscall wrapper.

### What this means for the shield theory

The shield payload *reports* tamper state (into its own struct), but something else *acts* on it — or the act is independent of the shield payload entirely. Candidate killers in order of likelihood:

1. **IL2CPP-resident integrity check.** Unity/Cygames managed code that pings the shield periodically and calls `Application.Quit()` / `System.exit()` on mismatch. This is the cleanest match for "clean exit 0" + "2 s after Hooking finished" + "hooks of libc exit never fire" + "shield flag stays zero because this check doesn't route through the shield's flag".
2. **Inline `svc` in `libnative.so` or `libmain.so`.** Prior analysis said "no inline `mov x8,#93/94; svc` patterns in libnative.so" but we have not re-run that scan against `libmain.so` or `libunity.so`.
3. **Java-side quit path.** `android.os.Process.killProcess(myPid())` or `System.exit(0)` from Java triggered by the IL2CPP check — would appear as clean exit 0 and not pass through libc.

### Files / binaries produced this session

- `scripts/shield_probe_addr.c` — verifies which mappings overlap the decoded struct address.
- `scripts/shield_flag_monitor.c` — 10 ms polling of the detection buffer across Uma's lifetime.
- `scripts/shield_svc_patch.c` — patches the single `svc #0` in the shield payload to `nop`.
- `/tmp/shield_live.bin` — fresh unpacked shield dump (94208 B, sha256 `b33faadd9b3447d699e7040b0b1967a0121214d0b40222bd2287e1aad25bdc56`).
- Memory: `project_hachimi_zygisk.md` updated with the flag-is-zero + svc-patch-doesn't-save findings.

---

## Revised plan: pivot up to IL2CPP

We have one viable tool with known behavior: **ZygiskFrida + 3000 ms delay** reliably gets a Frida session into Uma Global on BlueStacks without immediately dying. We have one data point: **patching `libnative.so` with Interceptor trampolines triggers a 1–2 s SIGSEGV-via-anti-RE**. We have one inference: **the IL2CPP JIT region probably isn't integrity-scanned by the shield** (if it were, Hachimi's il2cpp method-replacement hooks would have been caught the same way; instead Hachimi gets to `Hooking finished` before *something else* exits).

So the revised hook surface is: **IL2CPP methods, not native libnative functions.**

### WS-4 (REVISED AGAIN): Frida IL2CPP hook on the decrypted-packet receiver

**Goal:** hook the managed-code method in Uma that first sees a decrypted server response payload, copy the payload out, `send(msg, buf)` to the host.

**Approach:**

1. **Identify the target method.** Uma is Unity + IL2CPP. Server traffic is packed → AES-encrypted → LZ4-compressed in `libnative.so`, then handed up to C# for deserialization. The C# callsite that receives the decompressed-and-decrypted `byte[]` or `Stream` is where we hook. Candidates (named via reverse engineering):
   - `Gallop.WebViewManager.OnApiResponse(byte[])` (or similar — Gallop is Uma's top-level C# namespace)
   - `Cygames.Umamusume.Network.ResponseHandler.OnResponse(byte[])`
   - Any method whose first argument is `byte[]` and whose call sites include a msgpack unpacker (`MessagePackSerializer.Deserialize<T>`).
2. **Resolve the method from Frida.** Use `frida-il2cpp-bridge` (https://github.com/vfsfitvnm/frida-il2cpp-bridge) which exposes `Il2Cpp.domain.assembly(...).image.class(...).method(...)` — converts method name to runtime address by walking IL2CPP metadata. Works without needing symbolicated `libil2cpp.so`.
3. **Hook with `Interceptor.attach`** on the resolved runtime address. This is the same primitive that blew up on `libnative.so`, but the target now lives in the IL2CPP JIT region, which the shield does not appear to scan (evidence: Hachimi's existing IL2CPP hooks run for 2 s before the death path fires, and the death path is *not* SIGSEGV — it's clean exit, i.e. a different check).
4. **On hook fire:** copy the `byte[]` via `il2cpp_array_length` / `il2cpp_array_elements`, `send({type:'packet', size: n}, buf)` to host. Host-side driver is unchanged from Addendum 1's WS-4 (writes to `/tmp/carrot_relay/<ts>.msgpack`).

**Method discovery substream (offline, in parallel):**

- Pull `libil2cpp.so` (172 MB) + `global-metadata.dat` off the device.
- Run `Il2CppDumper` (https://github.com/Perfare/Il2CppDumper) to produce a `dump.cs` with all C# class/method names and their runtime addresses.
- Grep `dump.cs` for: `byte[]` args on methods in namespaces `Gallop.`/`Cygames.`/`Cute.`/anything containing `Response`/`Api`/`Server`/`Packet`.
- Cross-reference with UmaLauncher Python source (which knows the JP method names from 5+ years of CarrotJuicer work) — the Global build is likely the same C# with possibly renamed classes.
- Deliverable: a short-list of 3–5 candidate `(namespace, class, method)` triples. WS-4 tries each.

**Why this path is plausible despite Hachimi's failure:**

Hachimi died not because Hachimi's hooks were detected (they run long enough to complete init) — it died because it hooks *many* methods (the full translation suite: asset loading, text rendering, Unity lifecycle). Our plugin would hook *one* method (the response receiver) and do nothing else to the game's managed state. Smaller blast radius, smaller detection surface.

**Failure modes and fallbacks:**

- **IL2CPP hook also triggers the kill path.** Then we know the integrity check covers JIT-region code too. Fall back to (a) `Frida.Stalker` event-only tracing (no code patching), or (b) hardware breakpoints on ARM64 (4 available, no trampoline bytes), or (c) LSPatch (app-scoped injection without re-signing).
- **Method name can't be identified.** The `Il2CppDumper` output is 100k+ methods. If the grep doesn't converge, hook `MessagePackSerializer.Deserialize` itself — every packet passes through the deserializer, so it's a natural choke point. The `byte[]` arg is the raw msgpack payload we want.
- **Frida-il2cpp-bridge incompatible with BlueStacks kernel / anti-debug tracers.** Fall back to manual metadata walk: `libil2cpp.so` exports `il2cpp_domain_get`, `il2cpp_assembly_get_image`, `il2cpp_image_get_class`, etc. We call these via `NativeFunction` and walk the metadata ourselves.

---

## Open questions to resolve before WS-4

These block WS-4. Resolvable in ~1 session of investigation each.

1. **Does Hachimi-Edge's IL2CPP hook set contain an equivalent of the response-receiver method?** If so, we might avoid writing our own hook entirely — just fork Hachimi, strip the translation logic, keep the one hook, add our send-to-host logic. That's less code than a fresh plugin.
2. **What exactly kills Hachimi at `t = Hooking_finished + ~2 s`?** Eliminating the cause would also unblock the "minimal Hachimi fork" path. Proposals:
   - Install a Java.perform hook on `android.os.Process.killProcess` and `java.lang.System.exit` — catches the Java-side quit path.
   - Scan `libil2cpp.so` + `libunity.so` + `libmain.so` for inline `svc` patterns (we only scanned the shield payload this session).
   - Hook `MessagePackSerializer.Deserialize` first as a probe — if it runs once before the quit fires, the response path is alive until quit.
3. **Can the shield be made to *not* scan libnative?** Almost certainly no (it runs on a thread we can't easily kill without tripping the mutual-watchdog mechanism). Noted as a dead end.

---

## What stays from Addendum 1

- **WS-1 (frida-server + ZygiskFrida infra on BlueStacks)**: unchanged, confirmed working. 3000 ms start-up delay is load-bearing.
- **WS-2 (Frida TS agent scaffold + Python driver)**: unchanged. `frida_agent/` and `uma_trainer/perception/carrotjuicer/frida_driver.py` both exist and load cleanly.
- **WS-3 (schema)**: complete, unchanged — `uma_trainer/perception/carrotjuicer/schema/`.
- **WS-5 / WS-6 / WS-7 (host-side pipeline, GameState API, production integration)**: unchanged.

The only thing that changed is WS-4's hook target: from `LZ4_decompress_safe_ext` in `libnative.so` to an IL2CPP method in the JIT region.

---

## Non-goals (do not pursue in WS-4)

- **Hachimi full-suite revival.** Even if we solve the ~2 s quit path, Hachimi's blast radius (full translation suite, asset hooks, UI) is larger than our needs. Packet interception wants exactly one hook. Don't re-enable the rest.
- **Shield payload patching.** This session conclusively removed the shield payload from the suspect list. Do not patch writer / reader / struct / svc. They are load-bearing for shield's bookkeeping but not for Uma's exit.
- **APK re-signing of any kind.** `lib__4e06__.so`'s signature check at 132–149 ms is the firmest "dead path" we have. Never re-sign.

---

## TL;DR for a new agent coming in cold

- We have Frida access to Uma Global on BlueStacks. We cannot Interceptor-attach inside `libnative.so` (shield detects trampolines in 1–2 s).
- Shield payload itself (0x17000 rwxp region) is *not* the killer — its detection buffer stays zero, its syscall wrapper isn't on the exit path.
- Next lead: hook an IL2CPP method in Uma's JIT region (response-receiver or `MessagePackSerializer.Deserialize`) via `frida-il2cpp-bridge`. Smaller blast radius than Hachimi, potentially under the shield's integrity scanner.
- Host pipeline (schema / receiver / GameState / bot integration) is already built and waiting.
