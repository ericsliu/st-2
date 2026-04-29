# Packet Interception Spec — Addendum 3: IL2CPP pivot execution plan

**Status**: Extends Addendum 2. Does not supersede the WS-1/WS-2/WS-3/WS-5/WS-6/WS-7 content there. This addendum is entirely about sequencing and prioritizing the next investigation + WS-4 implementation session, and about fixing a target-selection bug in Addendum 2's WS-4.

**Date**: 2026-04-18

---

## What Addendum 2 got right and what needs revising

Right:
- The shield payload is not the killer — that's conclusively ruled out. Stop investigating shield internals.
- The pivot from `libnative.so` hooks to IL2CPP-method hooks is the right architectural move. The shield scans `libnative.so`'s executable pages; Hachimi's IL2CPP hooks survive long enough to finish installation, meaning something *else* kills Hachimi at t+2s, not the JIT patching itself.
- Frida + ZygiskFrida + 3000 ms delay is working infrastructure. Don't touch WS-1 / WS-2.
- Smaller blast radius than Hachimi. A single hook on the response path has a far smaller detection signature than Hachimi's hundreds of translation hooks.

Needs revising:
- **Target selection priority is inverted.** Addendum 2 names `Gallop.WebViewManager.OnApiResponse` and `Cygames.Umamusume.Network.ResponseHandler.OnResponse` as primary targets, with `MessagePackSerializer.Deserialize` as a fallback. This is backwards. Deserialize is a better primary target for the reasons below.
- **No explicit parallelism.** The two unblocking investigations (what kills Hachimi, and building the IL2CPP method shortlist) are independent and should run simultaneously. Addendum 2 lists them sequentially.
- **"Java-side quit path" is under-prioritized.** Option 3 in Addendum 2's open questions is actually the cheapest and most conclusive probe. Promote it to the first thing done next session.

---

## Revised WS-4 target selection

### Primary target: `MessagePackSerializer.Deserialize` (or the equivalent in Uma's MessagePack library)

**Why this is the right primary target:**

1. **Guaranteed on the response path.** Every server response is msgpack-encoded. The client cannot consume it without deserializing. There is no codepath that bypasses this.
2. **Stable signature across game updates.** MessagePack is a third-party library (likely `MessagePack-CSharp` by neuecc, the dominant .NET msgpack library). Its method signatures don't change when Cygames ships a game update. Hooking Cygames' own code means re-locating the method after every obfuscation pass.
3. **Discoverable without a 100k-method grep.** MessagePack-CSharp has well-known class names (`MessagePackSerializer`, `NonGenericMessagePackSerializer`) and method names (`Deserialize`, `Deserialize<T>`). These are non-obfuscated in most Unity builds because they're API surface the game code calls by name.
4. **The `byte[]` arg is literally the thing we want.** The first overload of `Deserialize` takes the raw msgpack bytes. That's our payload. No further processing needed to extract it.
5. **Filter-by-context is straightforward.** Yes, the deserializer will be called for other things (local save files, asset bundles, etc.), but we can filter by: call site (is the caller from the network stack?), payload size (network payloads are typically in a distinctive size range), or msgpack top-level structure (network responses have identifying keys like `data_headers`).

**Why Cygames-namespaced methods are a worse primary target:**

- Names like `OnApiResponse` are inferred from how other games structure their network code; Uma's actual class/method names may differ entirely.
- Requires pulling `libil2cpp.so` + `global-metadata.dat` and running Il2CppDumper before you can even start searching.
- The grep over 100k method signatures is unbounded — you don't know when you've found the right one until you hook it and verify.
- Obfuscation may have renamed the class to something meaningless (`a1.b2.C3.d4`).

**When to fall back to Cygames-namespaced targets:**

If `MessagePackSerializer.Deserialize` turns out to also trigger the kill path (meaning: IL2CPP hooks in general are detected, not just libnative ones), that's a much bigger problem — it means *no* managed-code hook is safe, and we have to move to hardware breakpoints or Stalker event-only tracing. At that point the specific method doesn't matter; all IL2CPP hooks are off the table.

If the deserializer hook works but the payload filtering is too noisy (too many non-network calls to wade through), then move up the stack to a Cygames-specific method. The filter noise tells us which namespace/class to look for (the caller on the stack at the moment of a network-shaped payload).

### Secondary target (only pursue if deserializer hook is too noisy)

`Gallop.*` namespace methods with `byte[]` as the first arg, filtered to those whose callers include a network stack frame. The Il2CppDumper shortlist approach from Addendum 2 still applies — just don't start with it.

---

## Priority-ordered investigation plan for next session

Run these in the order given. Stop at the first conclusive result for each branch and move on.

### Branch A: identify what kills Hachimi at t+2s (parallel with Branch B)

**Why this matters:** if the killer is a Java-side check, the fix is trivial and unblocks the "minimal Hachimi fork" path (strip translation hooks, keep the one hook we need, ship). If the killer is an inline `svc` in `libunity.so` or `libmain.so`, we patch it. If it's an IL2CPP-resident check that calls a managed quit method, we have to either hook that method to suppress the quit, or move to non-modifying analysis (Stalker, hardware breakpoints).

Steps, in order:

**A1. Java-side quit hook (cheap, conclusive, do first).**

From the Frida agent, add a `Java.perform` block that hooks:
- `android.os.Process.killProcess` — log PID arg + backtrace, then call original
- `java.lang.System.exit` — log exit code + backtrace, then call original
- `java.lang.Runtime.halt` — same
- `java.lang.Runtime.exit` — same

Re-enable Hachimi, launch Uma. If any of these fire during the "Hooking finished → t+2s → exit 0" window, we have our culprit. The backtrace tells us which managed method called it.

**A2. `svc` scan of loaded libraries (if A1 is silent).**

The Addendum 2 scan covered only the shield payload (`/tmp/shield_live.bin`). Repeat the same `d4000001` pattern search against:
- `libunity.so`
- `libmain.so`
- `libil2cpp.so` (this is huge — 172MB — so scan the `.text` section only)
- `libnative.so` (second pass; earlier scan was during a different session)

For each match, inspect the surrounding instructions. A "clean exit" candidate looks like `mov x8, #94` (or `#93` for exit_group) followed immediately by `svc #0`. Patch each match in turn via `/proc/<pid>/mem` and re-run; whichever patch prevents the t+2s exit is our killer.

**A3. IL2CPP quit-method hooks (if A1 and A2 are silent).**

Candidates:
- `UnityEngine.Application.Quit` (no args and int-arg overloads)
- `System.Environment.Exit`
- `System.Diagnostics.Process.Kill` (unlikely for self-kill but cheap to check)

Hook each via `frida-il2cpp-bridge`. If any fires in the death window, we've found it. This also tells us that our IL2CPP hook infrastructure works, which is the validation we need for Branch B.

### Branch B: build the IL2CPP method target list (parallel with Branch A)

Does not require a running game. Can execute fully offline.

**B1. Pull IL2CPP metadata off the device.**

```bash
adb shell 'run-as com.cygames.umamusume cp /data/app/.../lib/arm64/libil2cpp.so /sdcard/Download/'
adb shell 'run-as com.cygames.umamusume cp /data/app/.../assets/bin/Data/Managed/Metadata/global-metadata.dat /sdcard/Download/'
adb pull /sdcard/Download/libil2cpp.so ./il2cpp_artifacts/
adb pull /sdcard/Download/global-metadata.dat ./il2cpp_artifacts/
```

If `run-as` fails (non-debuggable APK), pull via `su` with the device's Magisk access.

**B2. Run Il2CppDumper.**

```bash
git clone https://github.com/Perfare/Il2CppDumper
cd Il2CppDumper
dotnet run ../il2cpp_artifacts/libil2cpp.so ../il2cpp_artifacts/global-metadata.dat ./dump_output/
```

Outputs: `dump.cs` (all classes + methods as C# syntax), `script.json` (IDA script for symbolication), `il2cpp.h` (C header for types).

**B3. Find the MessagePack deserializer method(s).**

```bash
grep -n 'MessagePackSerializer' dump_output/dump.cs | head -50
grep -n 'class.*Deserialize' dump_output/dump.cs | grep -i msgpack
```

Expect multiple overloads of `Deserialize`. The one to hook first: `public static T Deserialize<T>(byte[] bytes)` — the most common call site. Note its `Offset: 0x...` from dump.cs; that's the address within `libil2cpp.so`.

**B4. Backup: find Cygames-namespaced candidates.**

```bash
grep -nE '\b(Gallop|Cygames|Cute)\.' dump_output/dump.cs | grep -E 'Response|Api|Server|Packet|Receive' | head -50
```

Save the top 10 as a fallback shortlist. Do not hook these yet.

**Deliverable for Branch B:** A file `il2cpp_targets.md` listing the `MessagePackSerializer.Deserialize` overload addresses (primary) and the Cygames-namespaced candidate shortlist (fallback).

### Branch C: implement the hook (blocked on A1 finishing, not on A2/A3 or B completing)

The reason this is blocked on A1 specifically: if A1 finds the Java quit path, we can hook it to suppress the kill, re-enable Hachimi entirely, and skip writing our own IL2CPP hook — use Hachimi's existing infrastructure. That would cut WS-4 to almost nothing.

If A1 is silent (i.e. the quit path is native, not Java), then Branch C proceeds with the deserializer hook from Branch B while A2/A3 continue in parallel. Our hook works or it doesn't; the concurrent A2/A3 investigation is about making the hook survive, not about finding it.

**C1. Frida-il2cpp-bridge scaffolding.**

```typescript
import "frida-il2cpp-bridge";

Il2Cpp.perform(() => {
  const msgpackImage = Il2Cpp.domain.assembly("MessagePack").image;
  const serializer = msgpackImage.class("MessagePack.MessagePackSerializer");
  const deserialize = serializer.method<Il2Cpp.Object>("Deserialize", 1);

  deserialize.implementation = function (bytes: Il2Cpp.Array<number>) {
    const length = bytes.length;
    const buf = new Uint8Array(length);
    for (let i = 0; i < length; i++) buf[i] = bytes.get(i);
    send({ type: "packet", size: length }, buf.buffer);
    return this.method<Il2Cpp.Object>("Deserialize", 1).invoke(bytes);
  };
});
```

Exact assembly / class / method names must come from Branch B's dump. Adjust accordingly.

**C2. Host-side capture.**

The existing `uma_trainer/perception/carrotjuicer/frida_driver.py` already handles `on_message` for typed `send({type: "packet", ...}, buf)` envelopes. Verify it writes arriving payloads to `/tmp/carrot_relay/<ts>.msgpack` and round-trip through `schema/parser.py::parse_packet` to confirm they deserialize into `GamePacket` objects.

**C3. Noise filter.**

Deserializer will fire for non-network payloads. Filter on the host side:
- Size: drop payloads < 50 bytes (heartbeats, config) and > 10 MB (asset bundles).
- Structure: drop payloads whose parsed top-level dict doesn't match a known `PacketKind`. The WS-3 schema's `detect_packet_kind` will return `UNKNOWN` for non-network msgpack; treat that as a filter signal.

Log filtered-out counts so we can tune. If noise is > 90% of traffic, move to secondary target (Cygames-namespaced method).

**C4. Kill-path verification.**

Run the hooked game for 60 s. If Uma exits cleanly at t+2s like Hachimi, the IL2CPP hook itself is triggering the kill — proceed to Stalker / hardware-breakpoints fallback. If Uma survives and packets flow, WS-4 is done.

---

## Non-goals (still)

Same list as Addendum 2. Plus:
- **Il2CppDumper must NOT run inside the emulator.** The dumper is big and its Mono runtime will pollute the emulator's process table. Run it on the Mac host.
- **No hooking of more than one IL2CPP method until C4 passes.** Minimum blast radius is the whole point. If the single-hook version dies, adding more hooks makes it die faster.

---

## Decision matrix for the "what kills Hachimi" outcome

This is what Branch A produces. Use it to decide how to proceed on Branch C.

| Branch A outcome | Implication | Branch C action |
|---|---|---|
| Java quit path fires | Fixable with `Java.perform` hook to suppress it | Hook the Java method, re-enable full Hachimi, skip writing own hook |
| Inline `svc` in libunity/libmain/libil2cpp | Native kill, patchable | Patch the `svc` via `/proc/pid/mem`, then proceed with deserializer hook as planned |
| IL2CPP managed quit method fires | Native call from managed code, hookable | Hook the quit method to suppress, then proceed with deserializer hook |
| Nothing fires — kill source still unknown | Some other mechanism (timer thread, hardware WDT, kernel-level) | Fall back to Stalker event-only tracing or hardware breakpoints — no code modification at all |

The last row is the bad-case row. If we land there, the architectural assumption of "hook the game and read its memory" has to bend to "observe the game without touching it," which is a larger engineering lift.

---

## Time budget for next session

- Branch A1: 30-60 min. If it fires, stop A and move to Branch C with the fix.
- Branch A2 (conditional): 1-2 hr if needed.
- Branch A3 (conditional): 1 hr if needed.
- Branch B1+B2+B3: 1-2 hr offline while A runs.
- Branch B4: 30 min.
- Branch C1+C2: 2-3 hr once A1 is resolved.
- Branch C3+C4: 1-2 hr.

Full session: ~1 day of agent time, assuming parallel execution of A and B.

---

## TL;DR for the implementation agent

1. Hook the Java quit methods first. That single probe likely conclusively identifies what kills Hachimi, and the result determines the rest of the session.
2. In parallel, offline: pull IL2CPP metadata, run Il2CppDumper, find `MessagePackSerializer.Deserialize`'s address.
3. Primary WS-4 target is the msgpack deserializer, not a Cygames-namespaced method. It's guaranteed on the response path and library-stable.
4. If the Java hook finds the killer, the fix is trivial — suppress the quit, re-enable Hachimi, ship. If not, proceed with the deserializer hook and watch whether it also triggers the kill path.
5. Non-negotiables: one hook only, filter noise host-side via the existing WS-3 schema, do not touch the shield payload, do not re-sign APKs.
