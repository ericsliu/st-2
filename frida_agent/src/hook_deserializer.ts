// @ts-nocheck
/**
 * Branch C1 (from PACKET_INTERCEPTION_SPEC_ADDENDUM_3).
 *
 * Use frida-il2cpp-bridge to resolve MessagePack.LZ4MessagePackSerializer and
 * MessagePack.MessagePackSerializer, enumerate their Deserialize overloads,
 * and (optionally) install a logging hook on each.
 *
 * The first deployment is DISCOVERY ONLY — we want to confirm that the
 * HyperTech shield tolerates frida-il2cpp-bridge's domain/assembly walking
 * without tripping the PROT_NONE guard kill (see A1 result).
 *
 * Two entry points:
 *   - discoverDeserializers() — no hooks, just prints class/methods
 *   - installDeserializerHooks() — wraps every matching Deserialize overload
 */

import "frida-il2cpp-bridge";

type InvocationArguments = any;

const TARGET_CLASSES = [
    "MessagePack.LZ4MessagePackSerializer",
    "MessagePack.MessagePackSerializer",
];
const TARGET_ASSEMBLY = "MessagePack";

function summarizeMethod(m: any): Record<string, unknown> {
    let params: string[] = [];
    try {
        params = m.parameters.map((p: any) => {
            try {
                return `${p.type?.name ?? "?"} ${p.name ?? "?"}`;
            } catch (_) {
                return "?";
            }
        });
    } catch (_) {
        params = ["<err>"];
    }
    let ret = "?";
    try { ret = m.returnType?.name ?? "?"; } catch (_) { /* */ }
    return {
        name: m.name,
        params,
        returnType: ret,
        isStatic: !!m.isStatic,
        isGeneric: !!m.isGeneric,
        virtualAddress: (() => {
            try { return m.virtualAddress.toString(); } catch (_) { return "?"; }
        })(),
    };
}

function findClass(klassName: string): any | null {
    try {
        const image = Il2Cpp.domain.assembly(TARGET_ASSEMBLY).image;
        send({ type: "il2cpp_image", assembly: TARGET_ASSEMBLY, name: image.name });
        const klass = image.class(klassName);
        return klass ?? null;
    } catch (e: any) {
        send({ type: "il2cpp_err", step: `class(${klassName})`, err: String(e?.message ?? e) });
        return null;
    }
}

export function discoverDeserializers(): void {
    send({ type: "il2cpp_phase", phase: "perform_start" });
    Il2Cpp.perform(() => {
        send({
            type: "il2cpp_ready",
            unityVersion: Il2Cpp.unityVersion,
            appId: (() => { try { return Il2Cpp.application.identifier; } catch (_) { return "?"; } })(),
            appVersion: (() => { try { return Il2Cpp.application.version; } catch (_) { return "?"; } })(),
        });

        for (const klassName of TARGET_CLASSES) {
            const klass = findClass(klassName);
            if (!klass) {
                send({ type: "il2cpp_class_missing", name: klassName });
                continue;
            }
            send({
                type: "il2cpp_class",
                name: klassName,
                fullName: klass.fullName,
                methodCount: klass.methods.length,
            });
            const deserializers = klass.methods.filter((m: any) => /Deserialize/i.test(m.name));
            for (const m of deserializers) {
                send({ type: "il2cpp_method", className: klassName, method: summarizeMethod(m) });
            }
        }
    }, "main");
}

/**
 * Install a single Interceptor.attach on the shared IL2CPP generic dispatch
 * stub used by MessagePack.LZ4MessagePackSerializer.Deserialize (and all the
 * other generic uninflated overloads — they share one virtualAddress).
 *
 * This is a litmus test: does the HyperTech shield scan libil2cpp .text for
 * Interceptor trampolines the way it scans libc? If Uma survives, we can
 * proceed to more targeted hooks. If it dies with the PAC-corruption
 * signature, we pivot to GC.choose / Il2Cpp.trace.
 */
export function hookGenericDispatchStub(): void {
    send({ type: "il2cpp_phase", phase: "stub_hook_perform_start" });
    Il2Cpp.perform(() => {
        const klass = findClass("MessagePack.LZ4MessagePackSerializer");
        if (!klass) {
            send({ type: "il2cpp_stub_err", step: "class_missing" });
            return;
        }
        const m = klass.methods.find((x: any) => /^Deserialize$/.test(x.name));
        if (!m) {
            send({ type: "il2cpp_stub_err", step: "method_missing" });
            return;
        }
        let vaStr = "?";
        let vaPtr: NativePointer;
        try {
            vaPtr = m.virtualAddress;
            vaStr = vaPtr.toString();
        } catch (e: any) {
            send({ type: "il2cpp_stub_err", step: "va_read", err: String(e?.message ?? e) });
            return;
        }
        send({ type: "il2cpp_stub_target", method: m.name, virtualAddress: vaStr });
        try {
            let seq = 0;
            Interceptor.attach(vaPtr, {
                onEnter(args) {
                    (this as any).seq = ++seq;
                    send({
                        type: "il2cpp_stub_enter",
                        seq: (this as any).seq,
                        x0: args[0].toString(),
                        x1: args[1].toString(),
                        x2: args[2].toString(),
                        x3: args[3].toString(),
                    });
                },
                onLeave(retval) {
                    send({
                        type: "il2cpp_stub_leave",
                        seq: (this as any).seq,
                        retval: retval.toString(),
                    });
                },
            });
            send({ type: "il2cpp_stub_attached", virtualAddress: vaStr });
        } catch (e: any) {
            send({ type: "il2cpp_stub_err", step: "attach", err: String(e?.message ?? e) });
        }
    }, "main");
}

/**
 * Stalker litmus test — does following a thread trip the shield? Stalker
 * recompiles basic blocks into a scratch buffer; the target .text is
 * supposed to stay untouched. If the shield's libil2cpp code scan catches
 * us anyway, Stalker is also blocked and we move to Option 2/3.
 *
 * We follow every thread (Process.enumerateThreads hides Frida's own, so
 * this covers the app threads we care about) with minimal event collection.
 */
function enumerateTaskTids(): number[] {
    const tids: number[] = [];
    try {
        const opendir = new NativeFunction(Module.getGlobalExportByName("opendir"), "pointer", ["pointer"]);
        const readdir = new NativeFunction(Module.getGlobalExportByName("readdir"), "pointer", ["pointer"]);
        const closedir = new NativeFunction(Module.getGlobalExportByName("closedir"), "int", ["pointer"]);
        const dir = opendir(Memory.allocUtf8String("/proc/self/task"));
        if (dir.isNull()) return tids;
        while (true) {
            const ent = readdir(dir);
            if (ent.isNull()) break;
            const name = ent.add(19).readCString();
            if (!name || name === "." || name === "..") continue;
            const tid = parseInt(name, 10);
            if (!isNaN(tid)) tids.push(tid);
        }
        closedir(dir);
    } catch (e: any) {
        send({ type: "stalker_enum_err", err: String(e?.message ?? e) });
    }
    return tids;
}

function readCommForTid(tid: number): string | null {
    try {
        const f = new File("/proc/self/task/" + tid + "/comm", "r");
        const s = f.readLine();
        f.close();
        return s.replace(/[\r\n\x00]/g, "").trim();
    } catch (_) { return null; }
}

// ---- Follow-set narrowing ---------------------------------------------------
// Unity/IL2CPP hot threads we want to Stalker.follow. Exact matches first, then
// prefix matches, then regex. The goal: keep the follow set small enough that
// BlueStacks stays responsive, while still covering every thread that can
// plausibly execute managed MessagePack code.
const FOLLOW_EXACT = new Set<string>([
    "UnityMain",
    "UnityGfxDone",
    "UnityChoreograph",
    // Linux /proc/<tid>/comm truncates to 15 chars — "UnityChoreograph"
    // (16 chars) shows up as "UnityChoreograp". Keep both so the whitelist
    // matches whether the kernel hands us the full or truncated name.
    "UnityChoreograp",
    "UnloadThread",
    "HttpClient",
    // IL2CPP thread-pool workers run generic dispatch for MessagePack
    // formatters; full name is "IL2CPP Threadpool Worker" → truncates to
    // "IL2CPP Threadpo" (15 chars).
    "IL2CPP Threadpo",
    // Unity Addressables loader threads — can execute MessagePack on asset
    // deserialize. Full names are "Loading.AsyncReadManager" and
    // "Loading.PreloadManager" → both truncate to 15 chars.
    "Loading.AsyncRe",
    "Loading.Preload",
]);
// Package prefix — Android main thread comm is typically a truncated package
// name (comm is capped at 15 chars, so "com.cygames.uma" is what we see).
const FOLLOW_PACKAGE_PREFIXES = [
    "com.cygames.um",
    "com.cygames.uma",
    // Linux comm truncates to 15 chars. For "com.cygames.umamusume" the kernel
    // can surface the trailing 15 chars as "games.umamusume" on child threads
    // spawned without explicit prctl(PR_SET_NAME). Multiple such tids appear
    // in Uma's process and any of them may host libnative.so libcurl/mbedTLS
    // I/O workers — follow them.
    "games.umamusu",
    "games.umamusume",
];
const FOLLOW_PREFIXES = [
    "Job.Worker",
    "Worker Pool",
    "NativeThreadPool",
    "tp-background",
    "OkHttp",
    "ConnectionP",
    "pool-",
    // Unity "Background Job.Worker N" threads — ~16 of them. Full name is
    // "Background Job.Worker 0"..N → truncates to "Background Job." (15
    // chars incl. trailing period). Prefix match catches every index.
    "Background Job.",
];
// Thread-N where N is a number — Java/Kotlin managed worker threads that may
// host JNI → IL2CPP reentry. NOT to be confused with Thread-JVM-* (renamed
// Frida threads) which we explicitly skip.
const FOLLOW_REGEX = [
    /^Thread-\d+$/,
];

// Hard-skip patterns — these outrank any keep rule.
const SKIP_FRIDA_PREFIX = "Thread-JVM-";
const SKIP_FRIDA_REGEX = /^gum-|^gmain$|^gdbus$|^pool-frida|^frida-/;
const SKIP_RENDER_EXACT = new Set<string>([
    "UnityGfxDeviceW",
    "ScriptChannel",
    "AudioTrack",
    "BKPM",
    "SDLThread",
    "VulkanThread",
]);
const SKIP_RENDER_PREFIXES = ["mali-"];
const SKIP_GC_IO_EXACT = new Set<string>([
    "Finalizer",
    "RenderThread",
    "hwuiTask",
    "GLThread",
    "FinalizerD",
    "ReferenceQueue",
]);
const SKIP_GC_IO_PREFIXES = ["binder:", "HwBinder:"];

function classifyComm(tid: number, comm: string | null, selfTid: number, mainPid: number): { keep: boolean; reason: string } {
    if (tid === selfTid) return { keep: false, reason: "self" };
    if (!comm) return { keep: false, reason: "comm_unreadable" };

    // Hard-skip first.
    if (comm.startsWith(SKIP_FRIDA_PREFIX)) return { keep: false, reason: "renamed_frida" };
    if (SKIP_FRIDA_REGEX.test(comm)) return { keep: false, reason: "frida_unrenamed" };
    if (SKIP_RENDER_EXACT.has(comm)) return { keep: false, reason: "render" };
    for (const p of SKIP_RENDER_PREFIXES) if (comm.startsWith(p)) return { keep: false, reason: "render_prefix" };
    if (SKIP_GC_IO_EXACT.has(comm)) return { keep: false, reason: "gc_io" };
    for (const p of SKIP_GC_IO_PREFIXES) if (comm.startsWith(p)) return { keep: false, reason: "gc_io_prefix" };

    // Keep rules.
    if (tid === mainPid) return { keep: true, reason: "main_pid" };
    if (FOLLOW_EXACT.has(comm)) return { keep: true, reason: "exact" };
    for (const p of FOLLOW_PACKAGE_PREFIXES) if (comm.startsWith(p)) return { keep: true, reason: "package_prefix" };
    for (const p of FOLLOW_PREFIXES) if (comm.startsWith(p)) return { keep: true, reason: "prefix" };
    for (const r of FOLLOW_REGEX) if (r.test(comm)) return { keep: true, reason: "regex" };

    return { keep: false, reason: "not_whitelisted" };
}

let _followPlanEmitted = false;
let _broadFollowPlanEmitted = false;

/**
 * Broad variant: follow EVERY thread except hard-skips (self, frida, render,
 * gc/io). No whitelist — useful when investigating which thread actually
 * executes a specific library, or when the library has no identifiable
 * caller thread name.
 */
function classifyCommBroad(tid: number, comm: string | null, selfTid: number): { keep: boolean; reason: string } {
    if (tid === selfTid) return { keep: false, reason: "self" };
    if (!comm) return { keep: true, reason: "comm_unreadable_kept" };
    if (comm.startsWith(SKIP_FRIDA_PREFIX)) return { keep: false, reason: "renamed_frida" };
    if (SKIP_FRIDA_REGEX.test(comm)) return { keep: false, reason: "frida_unrenamed" };
    if (SKIP_RENDER_EXACT.has(comm)) return { keep: false, reason: "render" };
    for (const p of SKIP_RENDER_PREFIXES) if (comm.startsWith(p)) return { keep: false, reason: "render_prefix" };
    if (SKIP_GC_IO_EXACT.has(comm)) return { keep: false, reason: "gc_io" };
    for (const p of SKIP_GC_IO_PREFIXES) if (comm.startsWith(p)) return { keep: false, reason: "gc_io_prefix" };
    return { keep: true, reason: "broad_keep" };
}

export function enumerateBroadFollowTargets(): { tid: number; comm: string }[] {
    const tids = enumerateTaskTids();
    const selfTid = Process.getCurrentThreadId();
    const kept: { tid: number; comm: string }[] = [];
    const skipped: { tid: number; comm: string; reason: string }[] = [];
    for (const tid of tids) {
        const comm = readCommForTid(tid);
        const v = classifyCommBroad(tid, comm, selfTid);
        if (v.keep) kept.push({ tid, comm: comm ?? "" });
        else skipped.push({ tid, comm: comm ?? "", reason: v.reason });
    }
    if (!_broadFollowPlanEmitted) {
        _broadFollowPlanEmitted = true;
        send({
            type: "stalker_broad_follow_plan",
            selfTid,
            total: tids.length,
            keptCount: kept.length,
            skippedCount: skipped.length,
            kept,
            skipped,
        });
    }
    return kept;
}

/**
 * Enumerate /proc/self/task, classify each thread via the whitelist above,
 * and return the list of tids that should be Stalker.followed. Emits a
 * single `stalker_follow_plan` send the first time it's called per agent
 * lifetime so the user can inspect what's being followed vs skipped.
 */
export function enumerateFollowTargets(): { tid: number; comm: string }[] {
    const tids = enumerateTaskTids();
    const selfTid = Process.getCurrentThreadId();
    const mainPid = Process.id;
    const kept: { tid: number; comm: string }[] = [];
    const skipped: { tid: number; comm: string; reason: string }[] = [];
    for (const tid of tids) {
        const comm = readCommForTid(tid);
        const verdict = classifyComm(tid, comm, selfTid, mainPid);
        if (verdict.keep) {
            kept.push({ tid, comm: comm ?? "" });
        } else {
            skipped.push({ tid, comm: comm ?? "", reason: verdict.reason });
        }
    }
    if (!_followPlanEmitted) {
        _followPlanEmitted = true;
        send({
            type: "stalker_follow_plan",
            selfTid,
            mainPid,
            total: tids.length,
            keptCount: kept.length,
            skippedCount: skipped.length,
            kept,
            skipped,
        });
    }
    return kept;
}

export function probeStalkerFollow(): void {
    send({ type: "stalker_phase", phase: "follow_start" });
    const targets = enumerateFollowTargets();
    send({ type: "stalker_threads_seen", count: targets.length, selfTid: Process.getCurrentThreadId() });
    let followed = 0;
    for (const t of targets) {
        try {
            Stalker.follow(t.tid, {
                events: { call: false, ret: false, exec: false, block: false, compile: false },
            });
            followed++;
        } catch (e: any) {
            send({ type: "stalker_follow_err", tid: t.tid, name: t.comm, err: String(e?.message ?? e) });
        }
    }
    send({ type: "stalker_followed", followed, skipped_count: targets.length - followed });
}

/**
 * If probeStalkerFollow() survives, this adds a Stalker call probe on the
 * shared generic dispatch VA. addCallProbe instruments the recompiled copy
 * only — should not modify libil2cpp .text.
 */
export function probeStalkerOnGenericDispatch(): void {
    send({ type: "stalker_phase", phase: "call_probe_start" });
    Il2Cpp.perform(() => {
        const klass = findClass("MessagePack.LZ4MessagePackSerializer");
        if (!klass) {
            send({ type: "stalker_err", step: "class_missing" });
            return;
        }
        const m = klass.methods.find((x: any) => /^Deserialize$/.test(x.name));
        if (!m) {
            send({ type: "stalker_err", step: "method_missing" });
            return;
        }
        let vaPtr: NativePointer;
        try { vaPtr = m.virtualAddress; }
        catch (e: any) { send({ type: "stalker_err", step: "va", err: String(e?.message ?? e) }); return; }
        send({ type: "stalker_call_target", va: vaPtr.toString() });

        let hits = 0;
        try {
            Stalker.addCallProbe(vaPtr, function (args: InvocationArguments) {
                hits++;
                if (hits <= 3) {
                    send({
                        type: "stalker_call_hit",
                        hits,
                        x0: args[0].toString(),
                        x1: args[1].toString(),
                    });
                }
            });
            send({ type: "stalker_call_probe_installed", va: vaPtr.toString() });
        } catch (e: any) {
            send({ type: "stalker_err", step: "addCallProbe", err: String(e?.message ?? e) });
        }
    }, "main");
}

/**
 * Unlike addCallProbe (direct CALL only), a Stalker transform fires
 * regardless of how execution reached the target — including indirect
 * BLR x8 which IL2CPP uses for generic method dispatch through the
 * Il2CppMethodInfo.methodPointer table.
 *
 * Usage: call probeStalkerFollow() first (it Stalker.follow's all threads),
 * then this to install a PC-based callout on the generic dispatch VA.
 */
export function probeStalkerTransformOnDispatch(): void {
    send({ type: "stalker_phase", phase: "transform_start" });
    Il2Cpp.perform(() => {
        const klass = findClass("MessagePack.LZ4MessagePackSerializer");
        if (!klass) { send({ type: "stalker_err", step: "class_missing" }); return; }
        const m = klass.methods.find((x: any) => /^Deserialize$/.test(x.name));
        if (!m) { send({ type: "stalker_err", step: "method_missing" }); return; }
        let target: NativePointer;
        try { target = m.virtualAddress; }
        catch (e: any) { send({ type: "stalker_err", step: "va", err: String(e?.message ?? e) }); return; }
        const targetStr = target.toString();
        send({ type: "stalker_transform_target", va: targetStr });

        // Re-follow every app thread WITH a transform that emits a callout
        // when execution reaches the target PC. This replaces any prior follow.
        const targets = enumerateFollowTargets();
        let hits = 0;
        let followed = 0;
        for (const t of targets) {
            const tid = t.tid;
            try { Stalker.unfollow(tid); } catch (_) { /* wasn't being followed */ }
            try {
                Stalker.follow(tid, {
                    transform: (iterator: any) => {
                        let ins: any;
                        while ((ins = iterator.next()) !== null) {
                            if (ins.address.equals(target)) {
                                iterator.putCallout((context: any) => {
                                    hits++;
                                    if (hits <= 5) {
                                        send({
                                            type: "stalker_transform_hit",
                                            hits,
                                            pc: context.pc.toString(),
                                            x0: context.x0.toString(),
                                            x1: context.x1.toString(),
                                            x2: context.x2.toString(),
                                            x3: context.x3.toString(),
                                        });
                                    }
                                });
                            }
                            iterator.keep();
                        }
                    },
                });
                followed++;
            } catch (e: any) {
                send({ type: "stalker_follow_err", tid, err: String(e?.message ?? e) });
            }
        }
        send({ type: "stalker_transform_followed", followed });

        // Periodic hit-count ping so we know the callout is alive even at 0 hits.
        setInterval(() => {
            send({ type: "stalker_transform_hits", hits });
        }, 3000);
    }, "main");
}

/**
 * Enumerate all classes in the MessagePack assembly, listing non-generic
 * methods with their virtualAddresses. Goal: find hook candidates that
 * (a) are on the response hot path, (b) have concrete (not-template) VAs.
 *
 * Good candidates: MessagePackBinary.ReadBytes, LZ4 decode entry, formatter
 * implementations for common types.
 */
export function findNonGenericMethodCandidates(): void {
    send({ type: "catalog_phase", phase: "start" });
    Il2Cpp.perform(() => {
        const seenVa = new Set<string>();
        const assemblies = ["MessagePack"];
        for (const asmName of assemblies) {
            let image: any;
            try { image = Il2Cpp.domain.assembly(asmName).image; }
            catch (e: any) { send({ type: "catalog_err", asm: asmName, err: String(e?.message ?? e) }); continue; }
            send({ type: "catalog_image", asm: asmName, classCount: image.classes?.length });
            const classes = image.classes ?? [];
            for (const klass of classes) {
                let methods: any[] = [];
                try { methods = klass.methods ?? []; } catch (_) { continue; }
                for (const m of methods) {
                    try {
                        if (m.isGeneric) continue;
                        const va = m.virtualAddress.toString();
                        if (va === "0x0") continue;
                        seenVa.add(va);
                        send({
                            type: "catalog_method",
                            klass: klass.fullName,
                            method: m.name,
                            va,
                            params: m.parameters.length,
                            ret: (() => { try { return m.returnType.name; } catch (_) { return "?"; } })(),
                        });
                    } catch (_) { /* skip */ }
                }
            }
        }
        send({ type: "catalog_done", uniqueVAs: seenVa.size });
    }, "main");
}

/**
 * Stalker call-probe on the non-generic 1-arg
 *   MessagePack.LZ4MessagePackSerializer.Decode(Byte[]) -> Byte[]
 * overload. This is the outer LZ4 decode entry point reached from the
 * MessagePackSerializer response pipeline; it has a concrete (not template)
 * VA so addCallProbe can target it directly.
 *
 * Flow:
 *   1. Il2Cpp.perform → find the class, filter Decode overloads where
 *      isGeneric===false and parameters.length===1 and the single param
 *      looks like a byte array (Byte[] / System.Byte[]). VA is looked up
 *      LIVE — do not hardcode, ASLR changes it per boot.
 *   2. Stalker.follow every app thread (skip self tid and any comm starting
 *      with Thread-JVM-, those are renamed Frida threads).
 *   3. Stalker.addCallProbe(decodeVa, onCall). args[0] is the Byte[] arg
 *      pointer (static method, no `this`). We try wrapping with
 *      Il2Cpp.Array for a clean .length / byte read; fall back to raw
 *      pointer + header offsets (ARM64 IL2CPP byte[] header: max_length at
 *      +0x18, data at +0x20).
 *   4. Rate limit: emit detailed `lz4_decode_call` for the first 10 hits,
 *      then `lz4_decode_stats` every 2s with count + simple len histogram.
 *
 * If addCallProbe returns zero hits while Uma is obviously talking to the
 * server, the method is likely reached only via indirect BLR out of the
 * generic dispatch glue — pivot to probeStalkerTransformOnDispatch style
 * (transform with iterator.putCallout at block start) against this VA.
 */
export function probeStalkerOnLz4Decode(): void {
    send({ type: "stalker_phase", phase: "lz4_decode_start" });
    Il2Cpp.perform(() => {
        const klass = findClass("MessagePack.LZ4MessagePackSerializer");
        if (!klass) {
            send({ type: "lz4_decode_err", step: "class_missing" });
            return;
        }

        // Find non-generic Decode(Byte[]) overload. Log every Decode overload
        // we see so we can sanity-check the pick in the log.
        let chosen: any = null;
        const decodeMethods = klass.methods.filter((x: any) => /^Decode$/.test(x.name));
        for (const m of decodeMethods) {
            let paramsDesc: string[] = [];
            try {
                paramsDesc = m.parameters.map((p: any) => {
                    try { return p.type?.name ?? "?"; } catch (_) { return "?"; }
                });
            } catch (_) { paramsDesc = ["<err>"]; }
            let va = "?";
            try { va = m.virtualAddress.toString(); } catch (_) { /* */ }
            send({
                type: "lz4_decode_candidate",
                name: m.name,
                isGeneric: !!m.isGeneric,
                params: paramsDesc,
                va,
            });
            if (chosen) continue;
            try {
                if (m.isGeneric) continue;
                if (!m.parameters || m.parameters.length !== 1) continue;
                const pname = (m.parameters[0].type?.name ?? "").toString();
                if (/(Byte\[\]|System\.Byte\[\]|byte\[\])/.test(pname)) {
                    chosen = m;
                }
            } catch (_) { /* skip */ }
        }

        if (!chosen) {
            send({ type: "lz4_decode_err", step: "no_byte_array_overload" });
            return;
        }

        let target: NativePointer;
        try { target = chosen.virtualAddress; }
        catch (e: any) { send({ type: "lz4_decode_err", step: "va", err: String(e?.message ?? e) }); return; }
        const targetStr = target.toString();
        send({ type: "lz4_decode_target", va: targetStr, params: chosen.parameters.map((p: any) => { try { return p.type.name; } catch (_) { return "?"; } }) });

        // Follow app threads (narrowed whitelist — see enumerateFollowTargets).
        const targets = enumerateFollowTargets();
        let followed = 0;
        for (const t of targets) {
            try {
                Stalker.follow(t.tid, {
                    events: { call: false, ret: false, exec: false, block: false, compile: false },
                });
                followed++;
            } catch (e: any) {
                send({ type: "lz4_decode_follow_err", tid: t.tid, name: t.comm, err: String(e?.message ?? e) });
            }
        }
        send({ type: "lz4_decode_followed", followed });

        // Stats / rate-limit state.
        let hits = 0;
        const histBuckets = [0, 0, 0, 0, 0, 0]; // <=64, <=256, <=1K, <=4K, <=16K, >16K
        function bucketFor(n: number): number {
            if (n <= 64) return 0;
            if (n <= 256) return 1;
            if (n <= 1024) return 2;
            if (n <= 4096) return 3;
            if (n <= 16384) return 4;
            return 5;
        }

        try {
            Stalker.addCallProbe(target, function (args: InvocationArguments) {
                hits++;
                let len = -1;
                let headHex = "";
                const rawPtr = args[0];
                try {
                    // First try Il2Cpp.Array wrapper — cleanest, handles layout.
                    const arr = new (Il2Cpp as any).Array(rawPtr);
                    len = arr.length;
                    // Read up to 32 bytes of head via raw pointer offset (header 0x20).
                    const dataPtr = rawPtr.add(0x20);
                    const n = Math.min(len, 32);
                    if (n > 0) {
                        const bytes = dataPtr.readByteArray(n);
                        if (bytes) {
                            const u8 = new Uint8Array(bytes);
                            let h = "";
                            for (let i = 0; i < u8.length; i++) {
                                const b = u8[i].toString(16);
                                h += b.length === 1 ? "0" + b : b;
                            }
                            headHex = h;
                        }
                    }
                } catch (_) {
                    // Fallback: raw read of max_length at +0x18, data at +0x20.
                    try {
                        len = rawPtr.add(0x18).readU32();
                        const n = Math.min(len, 32);
                        if (n > 0) {
                            const bytes = rawPtr.add(0x20).readByteArray(n);
                            if (bytes) {
                                const u8 = new Uint8Array(bytes);
                                let h = "";
                                for (let i = 0; i < u8.length; i++) {
                                    const b = u8[i].toString(16);
                                    h += b.length === 1 ? "0" + b : b;
                                }
                                headHex = h;
                            }
                        }
                    } catch (_) { /* give up on len/head */ }
                }
                if (len >= 0) {
                    try { histBuckets[bucketFor(len)]++; } catch (_) { /* */ }
                }
                if (hits <= 10) {
                    send({
                        type: "lz4_decode_call",
                        seq: hits,
                        va: targetStr,
                        ptr: rawPtr.toString(),
                        len,
                        head: headHex,
                    });
                }
            });
            send({ type: "lz4_decode_probe_installed", va: targetStr });
        } catch (e: any) {
            send({ type: "lz4_decode_err", step: "addCallProbe", err: String(e?.message ?? e) });
            return;
        }

        // Periodic stats ping — so we know the probe is alive even at 0 hits.
        setInterval(() => {
            send({
                type: "lz4_decode_stats",
                hits,
                hist: {
                    le64: histBuckets[0],
                    le256: histBuckets[1],
                    le1k: histBuckets[2],
                    le4k: histBuckets[3],
                    le16k: histBuckets[4],
                    gt16k: histBuckets[5],
                },
            });
        }, 2000);
    }, "main");
}

/**
 * Stalker call-probe on the raw LZ4 decompressor:
 *   MessagePack.LZ4.LZ4Codec.Decode(byte[] src, int srcOff, int srcLen,
 *                                   byte[] dst, int dstOff, int dstLen) -> int
 *
 * Catalog labelled this as "6 Int32" but the underlying managed signature
 * has 2 byte[] + 4 int32 arguments — we log every parameter type at lookup
 * time so we know exactly what we're seeing at runtime.
 *
 * Same Stalker.follow + addCallProbe pattern as probeStalkerOnLz4Decode: the
 * shield doesn't see the recompiled basic block, so this is invisible where
 * Interceptor.attach would be lethal.
 *
 * Inner LZ4_uncompress_safe64 (5 Int32 args) would see raw pointers only —
 * Decode is the better hook point because args[0]/args[3] are managed
 * byte[] we can read via IL2CPP array layout (len at +0x18, data at +0x20).
 */
export function probeStalkerOnLz4Codec(): void {
    send({ type: "stalker_phase", phase: "lz4codec_start" });
    Il2Cpp.perform(() => {
        const klass = findClass("MessagePack.LZ4.LZ4Codec");
        if (!klass) {
            send({ type: "lz4codec_err", step: "class_missing" });
            return;
        }

        // Enumerate all Decode overloads so we can sanity check the pick.
        const decodeMethods = klass.methods.filter(
            (x: any) => x.name === "Decode" && !x.isGeneric,
        );
        let chosen: any = null;
        for (const m of decodeMethods) {
            let paramsDesc: string[] = [];
            try {
                paramsDesc = m.parameters.map((p: any) => {
                    try { return p.type?.name ?? "?"; } catch (_) { return "?"; }
                });
            } catch (_) { paramsDesc = ["<err>"]; }
            let va = "?";
            try { va = m.virtualAddress.toString(); } catch (_) { /* */ }
            let ret = "?";
            try { ret = m.returnType?.name ?? "?"; } catch (_) { /* */ }
            send({
                type: "lz4codec_candidate",
                name: m.name,
                isGeneric: !!m.isGeneric,
                params: paramsDesc,
                ret,
                va,
            });
            if (!chosen && m.parameters && m.parameters.length === 6) {
                chosen = m;
            }
        }

        if (!chosen && decodeMethods.length === 1) {
            chosen = decodeMethods[0];
        }

        if (!chosen) {
            send({
                type: "lz4codec_err",
                step: "no_6arg_decode",
                overloads: decodeMethods.length,
            });
            return;
        }

        let target: NativePointer;
        try { target = chosen.virtualAddress; }
        catch (e: any) { send({ type: "lz4codec_err", step: "va", err: String(e?.message ?? e) }); return; }
        const targetStr = target.toString();
        const paramTypes = chosen.parameters.map((p: any) => {
            try { return p.type?.name ?? "?"; } catch (_) { return "?"; }
        });
        let retType = "?";
        try { retType = chosen.returnType?.name ?? "?"; } catch (_) { /* */ }
        send({
            type: "lz4codec_resolved",
            va: targetStr,
            paramTypes,
            retType,
        });

        // Follow every app thread (narrowed whitelist — see enumerateFollowTargets).
        const targets = enumerateFollowTargets();
        let followed = 0;
        for (const t of targets) {
            try {
                Stalker.follow(t.tid, {
                    events: { call: false, ret: false, exec: false, block: false, compile: false },
                });
                followed++;
            } catch (e: any) {
                send({ type: "lz4codec_follow_err", tid: t.tid, name: t.comm, err: String(e?.message ?? e) });
            }
        }
        send({ type: "lz4codec_followed", followed });

        // Rate-limit state.
        let hits = 0;
        const uniqueSrcLens = new Set<number>();
        let srcLenSum = 0;
        let srcLenSamples = 0;

        function hexify(buf: ArrayBuffer | null): string {
            if (!buf) return "";
            const u8 = new Uint8Array(buf);
            let h = "";
            for (let i = 0; i < u8.length; i++) {
                const b = u8[i].toString(16);
                h += b.length === 1 ? "0" + b : b;
            }
            return h;
        }

        try {
            Stalker.addCallProbe(target, function (args: InvocationArguments) {
                hits++;
                const srcPtr = args[0];
                let srcOff = -1;
                let srcLen = -1;
                const dstPtr = args[3];
                let dstOff = -1;
                let dstLen = -1;
                try { srcOff = args[1].toInt32(); } catch (_) { /* */ }
                try { srcLen = args[2].toInt32(); } catch (_) { /* */ }
                try { dstOff = args[4].toInt32(); } catch (_) { /* */ }
                try { dstLen = args[5].toInt32(); } catch (_) { /* */ }

                if (srcLen >= 0) {
                    uniqueSrcLens.add(srcLen);
                    srcLenSum += srcLen;
                    srcLenSamples++;
                }

                if (hits <= 10) {
                    let srcHead = "";
                    let srcArrLen = -1;
                    try {
                        // Only probe if srcPtr looks like a pointer (>= 0x10000).
                        const asInt = srcPtr.toUInt32();
                        const looksPtr = !srcPtr.isNull() && (asInt === 0 || asInt >= 0x10000);
                        if (looksPtr) {
                            try { srcArrLen = srcPtr.add(0x18).readU32(); } catch (_) { /* */ }
                            const readOff = Math.max(0, srcOff);
                            const n = 32;
                            const bytes = srcPtr.add(0x20).add(readOff).readByteArray(n);
                            srcHead = hexify(bytes);
                        }
                    } catch (_) { /* first-call junk is fine */ }

                    send({
                        type: "lz4codec_call",
                        seq: hits,
                        va: targetStr,
                        srcPtr: srcPtr.toString(),
                        srcOff,
                        srcLen,
                        srcArrLen,
                        dstPtr: dstPtr.toString(),
                        dstOff,
                        dstLen,
                        srcHead,
                    });
                }
            });
            send({ type: "lz4codec_probe_installed", va: targetStr });
        } catch (e: any) {
            send({ type: "lz4codec_err", step: "addCallProbe", err: String(e?.message ?? e) });
            return;
        }

        // Periodic stats ping — emits even when hits=0 so we know it's alive.
        setInterval(() => {
            // Keep the emitted list bounded — send at most 16 representative lens.
            const lens: number[] = [];
            let i = 0;
            for (const v of uniqueSrcLens) {
                lens.push(v);
                if (++i >= 16) break;
            }
            const avg = srcLenSamples > 0 ? Math.round(srcLenSum / srcLenSamples) : 0;
            send({
                type: "lz4codec_stats",
                hits,
                uniqueSrcLens: lens,
                uniqueSrcLenCount: uniqueSrcLens.size,
                avgSrcLen: avg,
            });
        }, 2000);
    }, "main");
}

/**
 * Transform-mode variant of probeStalkerOnLz4Codec.
 *
 * Prior addCallProbe runs got zero hits — diagnosis: IL2CPP dispatches
 * LZ4Codec.Decode through `BLR x8` with a vtable-loaded method pointer, and
 * Stalker.addCallProbe only matches direct/known-at-install-time call
 * targets. A Stalker transform runs per-block and inspects PC, so it catches
 * indirect jumps.
 *
 * Strategy:
 *   1. Il2Cpp.perform — resolve MessagePack.LZ4.LZ4Codec.Decode (6-arg,
 *      non-generic) → live absolute VA.
 *   2. enumerateFollowTargets() → narrowed whitelist.
 *   3. For each kept tid, Stalker.follow with a transform that compares the
 *      first instruction of every block against the target VA. On match,
 *      iterator.putCallout BEFORE iterator.keep(). Always call iterator.keep().
 *   4. Callout reads x0..x5 (ARM64 ABI, static method: byte[] src, int srcOff,
 *      int srcLen, byte[] dst, int dstOff, int dstLen). First 10 hits emit
 *      detailed `lz4codec_xform_hit`; after that, a 2s interval emits
 *      `lz4codec_xform_stats`.
 *   5. Boot-once guard so a repeated RPC doesn't stack transforms.
 */
let _lz4codecXformBooted = false;
export function probeStalkerTransformOnLz4Codec(): void {
    if (_lz4codecXformBooted) {
        send({ type: "lz4codec_xform_err", step: "already_booted" });
        return;
    }
    _lz4codecXformBooted = true;

    send({ type: "stalker_phase", phase: "lz4codec_xform_start" });
    Il2Cpp.perform(() => {
        const klass = findClass("MessagePack.LZ4.LZ4Codec");
        if (!klass) {
            send({ type: "lz4codec_xform_err", step: "class_missing" });
            return;
        }

        const decodeMethods = klass.methods.filter(
            (x: any) => x.name === "Decode" && !x.isGeneric,
        );
        let chosen: any = null;
        for (const m of decodeMethods) {
            let paramsDesc: string[] = [];
            try {
                paramsDesc = m.parameters.map((p: any) => {
                    try { return p.type?.name ?? "?"; } catch (_) { return "?"; }
                });
            } catch (_) { paramsDesc = ["<err>"]; }
            let va = "?";
            try { va = m.virtualAddress.toString(); } catch (_) { /* */ }
            send({
                type: "lz4codec_xform_candidate",
                name: m.name,
                isGeneric: !!m.isGeneric,
                params: paramsDesc,
                va,
            });
            if (!chosen && m.parameters && m.parameters.length === 6) {
                chosen = m;
            }
        }
        if (!chosen && decodeMethods.length === 1) {
            chosen = decodeMethods[0];
        }
        if (!chosen) {
            send({
                type: "lz4codec_xform_err",
                step: "no_6arg_decode",
                overloads: decodeMethods.length,
            });
            return;
        }

        let target: NativePointer;
        try { target = chosen.virtualAddress; }
        catch (e: any) {
            send({ type: "lz4codec_xform_err", step: "va", err: String(e?.message ?? e) });
            return;
        }
        const targetStr = target.toString();
        const paramTypes = chosen.parameters.map((p: any) => {
            try { return p.type?.name ?? "?"; } catch (_) { return "?"; }
        });
        let retType = "?";
        try { retType = chosen.returnType?.name ?? "?"; } catch (_) { /* */ }
        send({
            type: "lz4codec_xform_resolved",
            va: targetStr,
            paramTypes,
            retType,
        });

        // Hit state, shared across all followed threads and the stats timer.
        let hits = 0;
        const srcLenCounts: Record<number, number> = {};

        function hexify(buf: ArrayBuffer | null): string {
            if (!buf) return "";
            const u8 = new Uint8Array(buf);
            let h = "";
            for (let i = 0; i < u8.length; i++) {
                const b = u8[i].toString(16);
                h += b.length === 1 ? "0" + b : b;
            }
            return h;
        }

        function callout(context: any): void {
            hits++;
            const seq = hits;
            const srcPtr = context.x0;
            const dstPtr = context.x3;
            let srcOff = -1;
            let srcLen = -1;
            let dstOff = -1;
            let dstLen = -1;
            try { srcOff = context.x1.toInt32(); } catch (_) { /* */ }
            try { srcLen = context.x2.toInt32(); } catch (_) { /* */ }
            try { dstOff = context.x4.toInt32(); } catch (_) { /* */ }
            try { dstLen = context.x5.toInt32(); } catch (_) { /* */ }

            if (srcLen >= 0) {
                srcLenCounts[srcLen] = (srcLenCounts[srcLen] || 0) + 1;
            }

            if (seq <= 10) {
                let srcHead = "";
                let srcArrLen = -1;
                try {
                    const asU = srcPtr.toUInt32();
                    const looksPtr = !srcPtr.isNull() && (asU === 0 || asU >= 0x10000);
                    if (looksPtr) {
                        try { srcArrLen = srcPtr.add(0x18).readU32(); } catch (_) { /* */ }
                        const readOff = Math.max(0, srcOff);
                        const n = Math.min(32, srcLen > 0 ? srcLen : 32);
                        if (n > 0) {
                            const bytes = srcPtr.add(0x20).add(readOff).readByteArray(n);
                            srcHead = hexify(bytes);
                        }
                    }
                } catch (_) { /* first-call junk is fine */ }

                send({
                    type: "lz4codec_xform_hit",
                    seq,
                    va: targetStr,
                    srcPtr: srcPtr.toString(),
                    srcOff,
                    srcLen,
                    srcArrLen,
                    dstPtr: dstPtr.toString(),
                    dstOff,
                    dstLen,
                    srcHead,
                });
            }
        }

        const followTargets = enumerateFollowTargets();
        let followed = 0;
        for (const t of followTargets) {
            const tid = t.tid;
            try { Stalker.unfollow(tid); } catch (_) { /* */ }
            try {
                Stalker.follow(tid, {
                    transform: (iterator: any) => {
                        let ins: any;
                        // Only test the first instruction of each block —
                        // target is a function entry, so block.start==target
                        // is the only interesting case.
                        let first = true;
                        while ((ins = iterator.next()) !== null) {
                            if (first && ins.address.equals(target)) {
                                iterator.putCallout(callout);
                            }
                            first = false;
                            iterator.keep();
                        }
                    },
                });
                followed++;
            } catch (e: any) {
                send({ type: "lz4codec_xform_follow_err", tid, name: t.comm, err: String(e?.message ?? e) });
            }
        }
        send({ type: "lz4codec_xform_followed", followed, target: targetStr });

        // Stats heartbeat — every 2s, emit hit count + top-5 srcLens.
        setInterval(() => {
            const entries = Object.keys(srcLenCounts)
                .map((k) => [parseInt(k, 10), srcLenCounts[parseInt(k, 10)]] as [number, number])
                .sort((a, b) => b[1] - a[1])
                .slice(0, 5)
                .map(([len, n]) => ({ len, n }));
            send({
                type: "lz4codec_xform_stats",
                hits,
                uniqueSrcLens: entries,
                uniqueSrcLenCount: Object.keys(srcLenCounts).length,
            });
        }, 2000);
    }, "main");
}

/**
 * Transform-mode Stalker probe on MessagePack.MessagePackBinary.ReadBytes.
 *
 * ReadBytes is non-generic, too large to AOT-inline, and sits on nearly every
 * deserialize path for byte-blob extraction. If transform fires here, the
 * Stalker transform primitive is proven working end-to-end. If it doesn't,
 * the primitive itself is broken and we pivot to native-layer hooks
 * (SSL_read off libil2cpp).
 *
 * Signature (MessagePack-CSharp standard):
 *   static byte[] ReadBytes(byte[] bytes, int offset, out int readSize)
 *
 * ARM64 static call: x0 = byte[] ptr, x1 = int offset, x2 = out int* readSize.
 * IL2CPP byte[] layout: length (u32) at +0x18, data at +0x20.
 */
let _mpReadBytesXformBooted = false;
export function probeStalkerTransformOnMpReadBytes(): void {
    if (_mpReadBytesXformBooted) {
        send({ type: "mp_readbytes_xform_err", step: "already_booted" });
        return;
    }
    _mpReadBytesXformBooted = true;

    send({ type: "stalker_phase", phase: "mp_readbytes_xform_start" });
    Il2Cpp.perform(() => {
        const klass = findClass("MessagePack.MessagePackBinary");
        if (!klass) {
            send({ type: "mp_readbytes_xform_err", step: "class_missing" });
            return;
        }

        const readBytesMethods = klass.methods.filter(
            (x: any) => x.name === "ReadBytes" && !x.isGeneric,
        );
        let chosen: any = null;
        for (const m of readBytesMethods) {
            let paramsDesc: string[] = [];
            try {
                paramsDesc = m.parameters.map((p: any) => {
                    try { return p.type?.name ?? "?"; } catch (_) { return "?"; }
                });
            } catch (_) { paramsDesc = ["<err>"]; }
            let va = "?";
            try { va = m.virtualAddress.toString(); } catch (_) { /* */ }
            send({
                type: "mp_readbytes_xform_candidate",
                name: m.name,
                isGeneric: !!m.isGeneric,
                params: paramsDesc,
                va,
            });
            if (!chosen && m.parameters && m.parameters.length === 3) {
                chosen = m;
            }
        }
        if (!chosen && readBytesMethods.length === 1) {
            chosen = readBytesMethods[0];
        }
        if (!chosen) {
            send({
                type: "mp_readbytes_xform_err",
                step: "no_3arg_readbytes",
                overloads: readBytesMethods.length,
            });
            return;
        }

        let target: NativePointer;
        try { target = chosen.virtualAddress; }
        catch (e: any) {
            send({ type: "mp_readbytes_xform_err", step: "va", err: String(e?.message ?? e) });
            return;
        }
        const targetStr = target.toString();
        const paramTypes = chosen.parameters.map((p: any) => {
            try { return p.type?.name ?? "?"; } catch (_) { return "?"; }
        });
        let retType = "?";
        try { retType = chosen.returnType?.name ?? "?"; } catch (_) { /* */ }
        send({
            type: "mp_readbytes_xform_resolved",
            va: targetStr,
            paramTypes,
            retType,
        });

        // Hit state shared across threads + stats timer.
        let hits = 0;
        const offsetCounts: Record<number, number> = {};

        function hexify(buf: ArrayBuffer | null): string {
            if (!buf) return "";
            const u8 = new Uint8Array(buf);
            let h = "";
            for (let i = 0; i < u8.length; i++) {
                const b = u8[i].toString(16);
                h += b.length === 1 ? "0" + b : b;
            }
            return h;
        }

        function callout(context: any): void {
            hits++;
            const seq = hits;
            const srcPtr = context.x0;
            let offset = -1;
            try { offset = context.x1.toInt32(); } catch (_) { /* */ }

            if (offset >= 0) {
                offsetCounts[offset] = (offsetCounts[offset] || 0) + 1;
            }

            if (seq <= 10) {
                let arrayLen = -1;
                let head = "";
                try {
                    const asU = srcPtr.toUInt32();
                    const looksPtr = !srcPtr.isNull() && (asU === 0 || asU >= 0x10000);
                    if (looksPtr) {
                        try { arrayLen = srcPtr.add(0x18).readU32(); } catch (_) { /* */ }
                        const readOff = Math.max(0, offset);
                        const n = 32;
                        try {
                            const bytes = srcPtr.add(0x20).add(readOff).readByteArray(n);
                            head = hexify(bytes);
                        } catch (_) { /* */ }
                    }
                } catch (_) { /* first-call junk is fine */ }

                send({
                    type: "mp_readbytes_xform_hit",
                    seq,
                    va: targetStr,
                    srcPtr: srcPtr.toString(),
                    offset,
                    arrayLen,
                    head,
                });
            }
        }

        const followTargets = enumerateFollowTargets();
        let followed = 0;
        for (const t of followTargets) {
            const tid = t.tid;
            try { Stalker.unfollow(tid); } catch (_) { /* */ }
            try {
                Stalker.follow(tid, {
                    transform: (iterator: any) => {
                        let ins: any;
                        let first = true;
                        while ((ins = iterator.next()) !== null) {
                            if (first && ins.address.equals(target)) {
                                iterator.putCallout(callout);
                            }
                            first = false;
                            iterator.keep();
                        }
                    },
                });
                followed++;
            } catch (e: any) {
                send({ type: "mp_readbytes_xform_follow_err", tid, name: t.comm, err: String(e?.message ?? e) });
            }
        }
        send({ type: "mp_readbytes_xform_followed", followed, target: targetStr });

        // Stats heartbeat every 2s — emits even when hits=0.
        setInterval(() => {
            const entries = Object.keys(offsetCounts)
                .map((k) => [parseInt(k, 10), offsetCounts[parseInt(k, 10)]] as [number, number])
                .sort((a, b) => b[1] - a[1])
                .slice(0, 5)
                .map(([off, n]) => ({ off, n }));
            send({
                type: "mp_readbytes_xform_stats",
                hits,
                uniqueOffsets: entries,
                uniqueOffsetCount: Object.keys(offsetCounts).length,
            });
        }, 2000);
    }, "main");
}

export function installDeserializerHooks(opts?: { maxSnapshot?: number }): void {
    const maxSnap = opts?.maxSnapshot ?? 128;
    let seq = 0;

    send({ type: "il2cpp_phase", phase: "hook_perform_start" });
    Il2Cpp.perform(() => {
        for (const klassName of TARGET_CLASSES) {
            const klass = findClass(klassName);
            if (!klass) continue;
            const deserializers = klass.methods.filter((m: any) => /^Deserialize/.test(m.name));
            for (const m of deserializers) {
                try {
                    // Skip generic methods — hooking them requires inflation.
                    if (m.isGeneric && !m.isInflated) {
                        send({ type: "il2cpp_hook_skip", reason: "generic_uninflated", className: klassName, method: m.name });
                        continue;
                    }
                    const summary = summarizeMethod(m);
                    const self = m;
                    m.implementation = function (...args: any[]) {
                        const mySeq = ++seq;
                        // Try to grab a byte[] if any arg is one.
                        const payload: any = {
                            type: "il2cpp_deserialize",
                            seq: mySeq,
                            className: klassName,
                            method: self.name,
                            nargs: args.length,
                        };
                        for (let i = 0; i < args.length; i++) {
                            const a = args[i];
                            try {
                                if (a && typeof a === "object" && (a as any).length !== undefined && (a as any).read !== undefined) {
                                    // Il2Cpp.Array-like
                                    const len = (a as any).length;
                                    payload[`arg${i}_len`] = len;
                                    // Not reading bytes yet — just log shape first.
                                } else {
                                    payload[`arg${i}`] = String(a);
                                }
                            } catch (_) {
                                payload[`arg${i}`] = "<readerr>";
                            }
                        }
                        send(payload);
                        const result = (self as any).invoke.apply(self, args);
                        send({ type: "il2cpp_deserialize_ret", seq: mySeq, retType: typeof result });
                        return result;
                    };
                    send({ type: "il2cpp_hook_installed", className: klassName, method: summary });
                } catch (e: any) {
                    send({ type: "il2cpp_hook_err", className: klassName, method: m.name, err: String(e?.message ?? e) });
                }
            }
        }
    }, "main");
}

// ---------------------------------------------------------------------------
// Gallop HTTP stack discovery — Uma's Cygames-layer HTTP wrapper.
// Per il2cpp_targets.md, every server call routes through Gallop.HttpHelper.
// Enumerate its methods (and SendTaskProxy nested class) so we can pick a
// Stalker-transform target for the next iteration. The runtime assembly is
// likely the top-level app assembly, not MessagePack; try a handful.
// ---------------------------------------------------------------------------

const GALLOP_CANDIDATE_ASSEMBLIES = [
    "Assembly-CSharp",
    "UnityEngine",
    "Cygames.CIDP",
    "mscorlib",
];
const GALLOP_CANDIDATE_CLASSES = [
    "Gallop.HttpHelper",
    "Gallop.HttpHelper+SendTaskProxy",
    "Gallop.ISendTask",
];

function findClassAnyAssembly(klassName: string): { klass: any; asm: string } | null {
    // Try the target assembly list first; fall back to scanning every assembly
    // in the domain.
    const tried: string[] = [];
    for (const asm of GALLOP_CANDIDATE_ASSEMBLIES) {
        tried.push(asm);
        try {
            const image = Il2Cpp.domain.assembly(asm).image;
            const k = image.class(klassName);
            if (k) return { klass: k, asm };
        } catch (_) { /* not in this assembly */ }
    }
    // Fallback: linear scan
    try {
        const assemblies = (Il2Cpp.domain as any).assemblies ?? [];
        for (const a of assemblies) {
            const name = a.name ?? "?";
            if (GALLOP_CANDIDATE_ASSEMBLIES.includes(name)) continue; // already tried
            tried.push(name);
            try {
                const image = a.image;
                const k = image.class(klassName);
                if (k) return { klass: k, asm: name };
            } catch (_) { /* */ }
        }
    } catch (_) { /* */ }
    send({ type: "gallop_class_search_failed", klass: klassName, tried });
    return null;
}

let _taskDeserializeXformBooted = false;

/**
 * Stalker.transform across every `Gallop.*Task.Deserialize(byte[]) -> bool`
 * method in the `umamusume.Http` assembly. These are ~700 concrete per-endpoint
 * response decoders where the byte[] arg is already decrypted + decompressed
 * plaintext MessagePack — the deepest boundary we can hook before game code
 * parses the payload into typed DTOs.
 *
 * Shape (discovered 2026-04-21): every Gallop.*Task class has exactly 7 methods
 * and NO shared base beyond System.Object. Each Deserialize overload is
 * non-generic, non-inflated, with a distinct virtualAddress. Stalker.transform
 * gates on `calloutMap.has(ins.address.toString())` at block-first-instruction
 * only; the callout reads X1 (body byte[]) using ARM64 IL2CPP array layout
 * (len at +0x18, data at +0x20).
 */
export function probeStalkerTransformOnTaskDeserialize(): void {
    if (_taskDeserializeXformBooted) {
        send({ type: "task_deserialize_xform_err", step: "already_booted" });
        return;
    }
    _taskDeserializeXformBooted = true;

    send({ type: "stalker_phase", phase: "task_deserialize_xform_start" });
    Il2Cpp.perform(() => {
        let image: any;
        try {
            image = Il2Cpp.domain.assembly("umamusume.Http").image;
        } catch (e: any) {
            send({ type: "task_deserialize_xform_err", step: "assembly", err: String(e?.message ?? e) });
            return;
        }
        let classes: any[] = [];
        try { classes = image.classes ?? []; } catch (_) { /* */ }

        const calloutMap = new Map<string, string>();
        let taskClasses = 0;
        let resolvedMethods = 0;
        for (const k of classes) {
            let kName = "";
            try { kName = k.fullName ?? k.name ?? ""; } catch (_) { /* */ }
            if (!/Task$/.test(kName)) continue;
            taskClasses++;
            let methods: any[] = [];
            try { methods = k.methods ?? []; } catch (_) { continue; }
            const m = methods.find((mm: any) => {
                if (mm.name !== "Deserialize" || mm.isGeneric) return false;
                let params: any[] = [];
                try { params = mm.parameters ?? []; } catch (_) { return false; }
                if (params.length !== 1) return false;
                try {
                    return (params[0].type?.name ?? "") === "System.Byte[]";
                } catch (_) { return false; }
            });
            if (!m) continue;
            let va: NativePointer;
            try { va = m.virtualAddress; } catch (_) { continue; }
            if (va.isNull()) continue;
            calloutMap.set(va.toString(), kName);
            resolvedMethods++;
        }
        send({
            type: "task_deserialize_xform_resolved",
            taskClasses,
            resolvedMethods,
            sample: Array.from(calloutMap.entries()).slice(0, 5).map(([va, kn]) => ({ va, klass: kn })),
        });
        if (resolvedMethods === 0) {
            send({ type: "task_deserialize_xform_err", step: "zero_methods_resolved" });
            return;
        }

        let hitCount = 0;
        const perKlassHits = new Map<string, number>();

        function hexify(buf: ArrayBuffer | null): string {
            if (!buf) return "";
            const u8 = new Uint8Array(buf);
            let h = "";
            for (let i = 0; i < u8.length; i++) {
                const b = u8[i].toString(16);
                h += b.length === 1 ? "0" + b : b;
            }
            return h;
        }

        function readByteArray(ptr: NativePointer, maxHead: number): { len: number; head: string } {
            let len = -1;
            let head = "";
            try {
                if (!ptr.isNull()) {
                    try { len = ptr.add(0x18).readU32(); } catch (_) { /* */ }
                    const n = Math.min(len >= 0 ? len : maxHead, maxHead);
                    if (n > 0) {
                        try {
                            const bytes = ptr.add(0x20).readByteArray(n);
                            head = hexify(bytes);
                        } catch (_) { /* */ }
                    }
                }
            } catch (_) { /* */ }
            return { len, head };
        }

        function deserializeCallout(context: any): void {
            hitCount++;
            const seq = hitCount;
            const pcStr = context.pc.toString();
            const kName = calloutMap.get(pcStr) ?? "?";
            perKlassHits.set(kName, (perKlassHits.get(kName) ?? 0) + 1);
            // Deserialize is an instance method: x0 = this, x1 = body byte[].
            const x1 = context.x1;
            const r = readByteArray(x1, 96);
            if (seq <= 60 || (perKlassHits.get(kName) ?? 0) <= 3) {
                send({
                    type: "task_deserialize_hit",
                    seq,
                    klass: kName,
                    ptr: x1.toString(),
                    len: r.len,
                    head: r.head,
                });
            }
        }

        const transform = (iterator: any) => {
            let ins: any;
            let first = true;
            while ((ins = iterator.next()) !== null) {
                if (first && calloutMap.has(ins.address.toString())) {
                    iterator.putCallout(deserializeCallout);
                }
                first = false;
                iterator.keep();
            }
        };

        const followedSet = new Set<number>();
        function followNew(): number {
            const targets = enumerateFollowTargets();
            let newlyFollowed = 0;
            for (const t of targets) {
                if (followedSet.has(t.tid)) continue;
                try {
                    Stalker.follow(t.tid, { transform });
                    followedSet.add(t.tid);
                    newlyFollowed++;
                } catch (e: any) {
                    send({
                        type: "task_deserialize_xform_follow_err",
                        tid: t.tid, name: t.comm, err: String(e?.message ?? e),
                    });
                }
            }
            return newlyFollowed;
        }

        const initial = followNew();
        send({ type: "task_deserialize_xform_followed", followed: initial });

        let tickCount = 0;
        setInterval(() => {
            const added = followNew();
            tickCount++;
            const topKlasses = Array.from(perKlassHits.entries())
                .sort((a, b) => b[1] - a[1])
                .slice(0, 8);
            send({
                type: "task_deserialize_xform_stats",
                hitCount,
                followedTotal: followedSet.size,
                newlyFollowed: added,
                tick: tickCount,
                topKlasses,
            });
        }, 2000);
    }, "main");
}

let _taskDeserializeInterceptBooted = false;

/**
 * Sanity probe: Interceptor.attach directly on every resolved
 * Gallop.*Task.Deserialize(byte[]) VA. If the Stalker transform probe
 * reports 0 hits but Interceptor.attach fires, the Stalker gating has a
 * bug (VA-formatting or thread-selection mismatch). If Interceptor.attach
 * also yields 0 hits over several minutes of live HTTP, the methods are
 * genuinely dead in this build and we need a different hook surface.
 *
 * Batches attach in chunks, emits one `task_deserialize_intercept_hit`
 * on first-call per class (dedup to keep the stream sane), and aggregate
 * counts every 2s via `task_deserialize_intercept_stats`.
 */
export function interceptAttachOnTaskDeserialize(maxAttach?: number): void {
    if (_taskDeserializeInterceptBooted) {
        send({ type: "task_deserialize_intercept_err", step: "already_booted" });
        return;
    }
    _taskDeserializeInterceptBooted = true;
    const cap = typeof maxAttach === "number" && maxAttach > 0 ? maxAttach : 50;

    send({ type: "stalker_phase", phase: "task_deserialize_intercept_start" });
    Il2Cpp.perform(() => {
        let image: any;
        try {
            image = Il2Cpp.domain.assembly("umamusume.Http").image;
        } catch (e: any) {
            send({ type: "task_deserialize_intercept_err", step: "assembly", err: String(e?.message ?? e) });
            return;
        }
        let classes: any[] = [];
        try { classes = image.classes ?? []; } catch (_) { /* */ }

        type Entry = { va: NativePointer; klass: string };
        const entries: Entry[] = [];
        for (const k of classes) {
            let kName = "";
            try { kName = k.fullName ?? k.name ?? ""; } catch (_) { /* */ }
            if (!/Task$/.test(kName)) continue;
            let methods: any[] = [];
            try { methods = k.methods ?? []; } catch (_) { continue; }
            const m = methods.find((mm: any) => {
                if (mm.name !== "Deserialize" || mm.isGeneric) return false;
                let params: any[] = [];
                try { params = mm.parameters ?? []; } catch (_) { return false; }
                if (params.length !== 1) return false;
                try {
                    return (params[0].type?.name ?? "") === "System.Byte[]";
                } catch (_) { return false; }
            });
            if (!m) continue;
            let va: NativePointer;
            try { va = m.virtualAddress; } catch (_) { continue; }
            if (va.isNull()) continue;
            entries.push({ va, klass: kName });
            if (entries.length >= cap) break;
        }

        send({
            type: "task_deserialize_intercept_resolved",
            attempted: entries.length,
            sample: entries.slice(0, 5).map((e) => ({ va: e.va.toString(), klass: e.klass })),
        });

        const perKlassHits = new Map<string, number>();
        let hitCount = 0;
        let attachOk = 0;
        let attachErr = 0;
        const seenKlasses = new Set<string>();

        for (const entry of entries) {
            const klassName = entry.klass;
            try {
                Interceptor.attach(entry.va, {
                    onEnter(args) {
                        hitCount++;
                        perKlassHits.set(klassName, (perKlassHits.get(klassName) ?? 0) + 1);
                        if (!seenKlasses.has(klassName)) {
                            seenKlasses.add(klassName);
                            let len = -1;
                            let head = "";
                            try {
                                const x1 = args[1];
                                if (!x1.isNull()) {
                                    try { len = x1.add(0x18).readU32(); } catch (_) { /* */ }
                                    const n = Math.min(len >= 0 ? len : 64, 64);
                                    if (n > 0) {
                                        try {
                                            const bytes = x1.add(0x20).readByteArray(n);
                                            if (bytes) {
                                                const u8 = new Uint8Array(bytes);
                                                let h = "";
                                                for (let i = 0; i < u8.length; i++) {
                                                    const b = u8[i].toString(16);
                                                    h += b.length === 1 ? "0" + b : b;
                                                }
                                                head = h;
                                            }
                                        } catch (_) { /* */ }
                                    }
                                }
                            } catch (_) { /* */ }
                            send({
                                type: "task_deserialize_intercept_hit",
                                klass: klassName,
                                seq: hitCount,
                                len,
                                head,
                            });
                        }
                    },
                });
                attachOk++;
            } catch (e: any) {
                attachErr++;
                if (attachErr <= 5) {
                    send({
                        type: "task_deserialize_intercept_attach_err",
                        klass: klassName,
                        va: entry.va.toString(),
                        err: String(e?.message ?? e),
                    });
                }
            }
        }

        send({
            type: "task_deserialize_intercept_ready",
            attachOk,
            attachErr,
        });

        let tickCount = 0;
        setInterval(() => {
            tickCount++;
            const topKlasses = Array.from(perKlassHits.entries())
                .sort((a, b) => b[1] - a[1])
                .slice(0, 8);
            send({
                type: "task_deserialize_intercept_stats",
                hitCount,
                tick: tickCount,
                topKlasses,
            });
        }, 2000);
    }, "main");
}

let _gallopHttpXformBooted = false;

/**
 * Stalker-transform probe targeting two concrete Gallop.HttpHelper methods:
 *
 *   CompressRequest(byte[] requestData) -> byte[]        (pre-encrypt)
 *   DecompressResponse(byte[] responseData) -> byte[]    (post-decrypt)
 *
 * Both are non-generic static methods with real distinct VAs (no shared
 * sentinel). Runs at the plaintext MessagePack boundary — gives us the full
 * server response payload without ever touching TLS. VAs are resolved at
 * probe time (ASLR-safe) via Il2Cpp.perform().
 *
 * Captures the incoming byte[] (x0) on entry: length at +0x18, data at +0x20
 * (IL2CPP array layout on ARM64).
 */
export function probeStalkerTransformOnGallopHttp(): void {
    if (_gallopHttpXformBooted) {
        send({ type: "gallop_http_xform_err", step: "already_booted" });
        return;
    }
    _gallopHttpXformBooted = true;

    send({ type: "stalker_phase", phase: "gallop_http_xform_start" });
    Il2Cpp.perform(() => {
        const hit = findClassAnyAssembly("Gallop.HttpHelper");
        if (!hit) {
            send({ type: "gallop_http_xform_err", step: "class_missing" });
            return;
        }
        const methods = hit.klass.methods ?? [];
        const compress = methods.find((m: any) =>
            m.name === "CompressRequest" && !m.isGeneric,
        );
        const decompress = methods.find((m: any) =>
            m.name === "DecompressResponse" && !m.isGeneric,
        );
        if (!compress || !decompress) {
            send({
                type: "gallop_http_xform_err",
                step: "methods_missing",
                hasCompress: !!compress,
                hasDecompress: !!decompress,
            });
            return;
        }
        let compressVa: NativePointer;
        let decompressVa: NativePointer;
        try {
            compressVa = compress.virtualAddress;
            decompressVa = decompress.virtualAddress;
        } catch (e: any) {
            send({ type: "gallop_http_xform_err", step: "va_read", err: String(e?.message ?? e) });
            return;
        }
        const compressStr = compressVa.toString();
        const decompressStr = decompressVa.toString();
        send({
            type: "gallop_http_xform_resolved",
            compress: compressStr,
            decompress: decompressStr,
        });

        let compressHits = 0;
        let decompressHits = 0;

        function hexify(buf: ArrayBuffer | null): string {
            if (!buf) return "";
            const u8 = new Uint8Array(buf);
            let h = "";
            for (let i = 0; i < u8.length; i++) {
                const b = u8[i].toString(16);
                h += b.length === 1 ? "0" + b : b;
            }
            return h;
        }

        function readByteArray(ptr: NativePointer, maxHead: number): { len: number; head: string } {
            let len = -1;
            let head = "";
            try {
                if (!ptr.isNull()) {
                    try { len = ptr.add(0x18).readU32(); } catch (_) { /* */ }
                    const n = Math.min(len >= 0 ? len : maxHead, maxHead);
                    if (n > 0) {
                        try {
                            const bytes = ptr.add(0x20).readByteArray(n);
                            head = hexify(bytes);
                        } catch (_) { /* */ }
                    }
                }
            } catch (_) { /* */ }
            return { len, head };
        }

        function compressCallout(context: any): void {
            compressHits++;
            const seq = compressHits;
            const x0 = context.x0;
            const r = readByteArray(x0, 64);
            if (seq <= 20) {
                send({
                    type: "gallop_compress_hit",
                    seq,
                    ptr: x0.toString(),
                    len: r.len,
                    head: r.head,
                });
            }
        }

        function decompressCallout(context: any): void {
            decompressHits++;
            const seq = decompressHits;
            const x0 = context.x0;
            const r = readByteArray(x0, 128);
            if (seq <= 20) {
                send({
                    type: "gallop_decompress_hit",
                    seq,
                    ptr: x0.toString(),
                    len: r.len,
                    head: r.head,
                });
            }
        }

        const transform = (iterator: any) => {
            let ins: any;
            let first = true;
            while ((ins = iterator.next()) !== null) {
                if (first) {
                    if (ins.address.equals(compressVa)) {
                        iterator.putCallout(compressCallout);
                    } else if (ins.address.equals(decompressVa)) {
                        iterator.putCallout(decompressCallout);
                    }
                }
                first = false;
                iterator.keep();
            }
        };

        const followedSet = new Set<number>();
        function followNew(): number {
            const targets = enumerateFollowTargets();
            let newlyFollowed = 0;
            for (const t of targets) {
                if (followedSet.has(t.tid)) continue;
                try {
                    Stalker.follow(t.tid, { transform });
                    followedSet.add(t.tid);
                    newlyFollowed++;
                } catch (e: any) {
                    send({
                        type: "gallop_http_xform_follow_err",
                        tid: t.tid, name: t.comm, err: String(e?.message ?? e),
                    });
                }
            }
            return newlyFollowed;
        }

        const initial = followNew();
        send({
            type: "gallop_http_xform_followed",
            followed: initial,
            compress: compressStr,
            decompress: decompressStr,
        });

        let tickCount = 0;
        setInterval(() => {
            const added = followNew();
            tickCount++;
            send({
                type: "gallop_http_xform_stats",
                compressHits,
                decompressHits,
                followedTotal: followedSet.size,
                newlyFollowed: added,
                tick: tickCount,
            });
        }, 2000);
    }, "main");
}

let _cryptAesXformBooted = false;

/**
 * Stalker-transform probe for Gallop.CryptAES byte[]→byte[] methods. Found by
 * `scanGallopCompressionMethods()` after CompressRequest/DecompressResponse
 * turned out to be dead code in this build.
 *
 * Hook targets (runtime-resolved, ASLR-safe):
 *   EncryptRJ256(byte[]) → byte[]
 *   DecryptRJ256(byte[]) → byte[]
 *   Decrypt(byte[])      → byte[]
 *
 * Reasoning: Cygames' Uma uses Rijndael-256 for HTTP body encryption. Byte-array
 * overloads are the binary-payload path (string overloads are likely legacy or
 * local-data). If any of these fire on the HTTP worker thread we've found the
 * encryption boundary — the input to Encrypt = pre-encrypt plaintext, the
 * output of Decrypt = post-decrypt plaintext.
 */
export function probeStalkerTransformOnCryptAes(): void {
    if (_cryptAesXformBooted) {
        send({ type: "cryptaes_xform_err", step: "already_booted" });
        return;
    }
    _cryptAesXformBooted = true;
    send({ type: "stalker_phase", phase: "cryptaes_xform_start" });

    Il2Cpp.perform(() => {
        const hit = findClassAnyAssembly("Gallop.CryptAES");
        if (!hit) {
            send({ type: "cryptaes_xform_err", step: "class_missing" });
            return;
        }
        const methods = hit.klass.methods ?? [];
        function findByteMethod(name: string): any | null {
            const candidates = methods.filter((m: any) => m.name === name && !m.isGeneric);
            for (const m of candidates) {
                try {
                    const params = m.parameters ?? [];
                    if (params.length === 1) {
                        const pn = params[0].type?.name ?? "";
                        if (pn === "System.Byte[]") return m;
                    }
                } catch (_) { /* */ }
            }
            return null;
        }
        const encryptRJ = findByteMethod("EncryptRJ256");
        const decryptRJ = findByteMethod("DecryptRJ256");
        const decryptLegacy = findByteMethod("Decrypt");
        if (!encryptRJ && !decryptRJ && !decryptLegacy) {
            send({ type: "cryptaes_xform_err", step: "no_byte_methods" });
            return;
        }

        type T = { name: string; va: NativePointer; vaStr: string; hits: number };
        const targets: T[] = [];
        function register(name: string, method: any): void {
            if (!method) return;
            try {
                const va = method.virtualAddress as NativePointer;
                targets.push({ name, va, vaStr: va.toString(), hits: 0 });
            } catch (_) { /* */ }
        }
        register("EncryptRJ256", encryptRJ);
        register("DecryptRJ256", decryptRJ);
        register("Decrypt", decryptLegacy);

        send({
            type: "cryptaes_xform_resolved",
            targets: targets.map((t) => ({ name: t.name, va: t.vaStr })),
        });

        function readByteArray(ptr: NativePointer, maxHead: number): { len: number; head: string } {
            let len = -1;
            let head = "";
            try {
                if (!ptr.isNull()) {
                    try { len = ptr.add(0x18).readU32(); } catch (_) { /* */ }
                    const n = Math.min(len >= 0 ? len : maxHead, maxHead);
                    if (n > 0) {
                        try {
                            const bytes = ptr.add(0x20).readByteArray(n);
                            if (bytes) {
                                const u8 = new Uint8Array(bytes);
                                let h = "";
                                for (let i = 0; i < u8.length; i++) {
                                    const b = u8[i].toString(16);
                                    h += b.length === 1 ? "0" + b : b;
                                }
                                head = h;
                            }
                        } catch (_) { /* */ }
                    }
                }
            } catch (_) { /* */ }
            return { len, head };
        }

        function makeCallout(t: T): (ctx: any) => void {
            return function (context: any) {
                t.hits++;
                const seq = t.hits;
                const x0 = context.x0;
                const r = readByteArray(x0, 128);
                if (seq <= 20) {
                    send({
                        type: "cryptaes_hit",
                        target: t.name,
                        seq,
                        ptr: x0.toString(),
                        len: r.len,
                        head: r.head,
                    });
                }
            };
        }
        const calloutMap = new Map<string, (ctx: any) => void>();
        for (const t of targets) calloutMap.set(t.vaStr, makeCallout(t));

        const transform = (iterator: any) => {
            let ins: any;
            let first = true;
            while ((ins = iterator.next()) !== null) {
                if (first) {
                    const addrStr = ins.address.toString();
                    const cb = calloutMap.get(addrStr);
                    if (cb) iterator.putCallout(cb);
                }
                first = false;
                iterator.keep();
            }
        };

        const followedSet = new Set<number>();
        function followNew(): number {
            const list = enumerateFollowTargets();
            let added = 0;
            for (const t of list) {
                if (followedSet.has(t.tid)) continue;
                try {
                    Stalker.follow(t.tid, { transform });
                    followedSet.add(t.tid);
                    added++;
                } catch (e: any) {
                    send({
                        type: "cryptaes_follow_err",
                        tid: t.tid, name: t.comm, err: String(e?.message ?? e),
                    });
                }
            }
            return added;
        }
        const initial = followNew();
        send({
            type: "cryptaes_xform_followed",
            followed: initial,
            targetCount: targets.length,
        });

        let tick = 0;
        setInterval(() => {
            const added = followNew();
            tick++;
            send({
                type: "cryptaes_xform_stats",
                tick,
                followedTotal: followedSet.size,
                newlyFollowed: added,
                hits: targets.map((t) => ({ name: t.name, hits: t.hits })),
            });
        }, 2000);
    }, "main");
}

/**
 * Broad scan: walk every class in the `umamusume` assembly and emit any class
 * that has a method whose name matches /compress|decompress|encrypt|decrypt|
 * coneshell|send.*request|handle.*response|lz4|msgpack|messagepack/i. Use this
 * to find alternate hook points after CompressRequest/DecompressResponse proved
 * to be dead code in this build.
 *
 * Conservative: skips mscorlib/UnityEngine/System/etc. to avoid noise and to
 * keep enumeration fast; only scans the game's own assembly.
 */
export function scanGallopCompressionMethods(): void {
    send({ type: "gallop_scan_phase", phase: "start" });
    // Strict method-name regex: compression/encryption verb prefixes plus
    // request/response wrappers. We emit ONLY methods whose name matches,
    // regardless of class (we already restrict to the `umamusume` assembly).
    const nameRe = /^(Compress|Decompress|Encrypt|Decrypt|Cipher|Pack|Unpack|Encode|Decode|Deflate|Inflate|ProcessRequest|ProcessResponse|HandleRequest|HandleResponse|BuildRequest|BuildResponse|PrepareRequest|PreparePayload|SendRequest|HandleReceive|Receive|SerializeRequest|DeserializeResponse|ReadResponse|WriteRequest|DispatchRequest)/;
    Il2Cpp.perform(() => {
        let image: any;
        try {
            image = Il2Cpp.domain.assembly("umamusume").image;
        } catch (e: any) {
            send({ type: "gallop_scan_err", step: "assembly", err: String(e?.message ?? e) });
            return;
        }
        let klassCount = 0;
        let matchedClasses = 0;
        let emittedMethods = 0;
        let classes: any[] = [];
        try { classes = image.classes ?? []; } catch (_) { /* */ }
        for (const k of classes) {
            klassCount++;
            let kName = "";
            try { kName = k.fullName ?? k.name ?? ""; } catch (_) { /* */ }
            let methods: any[] = [];
            try { methods = k.methods ?? []; } catch (_) { continue; }
            let emittedForThisClass = 0;
            for (const m of methods) {
                let mName = "";
                try { mName = m.name ?? ""; } catch (_) { /* */ }
                if (!nameRe.test(mName)) continue;
                let va = "?";
                try { va = m.virtualAddress.toString(); } catch (_) { /* */ }
                let paramsDesc: string[] = [];
                try {
                    paramsDesc = (m.parameters ?? []).map((p: any) => {
                        try { return `${p.name}:${p.type?.name ?? "?"}`; } catch (_) { return "?"; }
                    });
                } catch (_) { paramsDesc = ["<err>"]; }
                let retType = "?";
                try { retType = m.returnType?.name ?? "?"; } catch (_) { /* */ }
                send({
                    type: "gallop_scan_method",
                    klass: kName,
                    name: mName,
                    isGeneric: !!m.isGeneric,
                    isInflated: !!m.isInflated,
                    isStatic: !!m.isStatic,
                    params: paramsDesc,
                    returnType: retType,
                    va,
                });
                emittedMethods++;
                emittedForThisClass++;
                if (emittedMethods > 400) break;
            }
            if (emittedForThisClass > 0) matchedClasses++;
            if (emittedMethods > 400) break;
        }
        send({
            type: "gallop_scan_phase",
            phase: "done",
            klassCount,
            matchedClasses,
            emittedMethods,
        });
    }, "main");
}

/**
 * Enumerate every loaded IL2CPP assembly — one `assembly` event per assembly
 * with its class count. Used to find non-umamusume assemblies (Cygames.Coneshell,
 * custom crypto libs) where the real HTTP/compress/encrypt path might live.
 * Safe: reads metadata only, no Interceptor/Stalker attach.
 */
export function enumerateAllAssemblies(): void {
    send({ type: "asm_enum_phase", phase: "start" });
    Il2Cpp.perform(() => {
        let count = 0;
        let assemblies: any[] = [];
        try { assemblies = Il2Cpp.domain.assemblies ?? []; } catch (e: any) {
            send({ type: "asm_enum_err", step: "domain.assemblies", err: String(e?.message ?? e) });
            return;
        }
        for (const a of assemblies) {
            let name = "?";
            let classCount = 0;
            try { name = a.name ?? "?"; } catch (_) { /* */ }
            try { classCount = (a.image?.classes ?? []).length; } catch (_) { classCount = -1; }
            send({ type: "assembly", name, classCount });
            count++;
        }
        send({ type: "asm_enum_phase", phase: "done", count });
    }, "main");
}

/**
 * Broad sweep: scan EVERY IL2CPP assembly for methods whose name matches the
 * compress/encrypt/request/response verb regex. Emits one `asm_crypto_method`
 * event per match. Companion to `scanGallopCompressionMethods` which only
 * scans umamusume — this widens to Coneshell, Cygames.*, etc.
 */
export function scanAllAssembliesForCrypto(): void {
    send({ type: "asm_scan_phase", phase: "start" });
    const nameRe = /^(Compress|Decompress|Encrypt|Decrypt|Cipher|Pack|Unpack|Encode|Decode|Deflate|Inflate|ProcessRequest|ProcessResponse|HandleRequest|HandleResponse|BuildRequest|BuildResponse|PrepareRequest|PreparePayload|SendRequest|HandleReceive|Receive|SerializeRequest|DeserializeResponse|ReadResponse|WriteRequest|DispatchRequest|Seal|Open|Aes|Rj256|Rijndael)/i;
    // Skip these common framework assemblies — full of AES/Encrypt/Decrypt noise
    // from .NET/Unity/MessagePack that isn't Uma's path.
    const skipAsm = /^(mscorlib|System|System\.|UnityEngine|Unity\.|netstandard|MessagePack|Newtonsoft|Mono\.|Microsoft\.)/;
    Il2Cpp.perform(() => {
        let assemblies: any[] = [];
        try { assemblies = Il2Cpp.domain.assemblies ?? []; } catch (e: any) {
            send({ type: "asm_scan_err", step: "domain.assemblies", err: String(e?.message ?? e) });
            return;
        }
        let totalAsm = 0;
        let scannedAsm = 0;
        let totalMatches = 0;
        for (const a of assemblies) {
            totalAsm++;
            let asmName = "?";
            try { asmName = a.name ?? "?"; } catch (_) { /* */ }
            if (skipAsm.test(asmName)) continue;
            scannedAsm++;
            let classes: any[] = [];
            try { classes = a.image?.classes ?? []; } catch (_) { continue; }
            let asmMatches = 0;
            for (const k of classes) {
                let kName = "";
                try { kName = k.fullName ?? k.name ?? ""; } catch (_) { /* */ }
                let methods: any[] = [];
                try { methods = k.methods ?? []; } catch (_) { continue; }
                for (const m of methods) {
                    let mName = "";
                    try { mName = m.name ?? ""; } catch (_) { /* */ }
                    if (!nameRe.test(mName)) continue;
                    let va = "?";
                    try { va = m.virtualAddress.toString(); } catch (_) { /* */ }
                    let paramsDesc: string[] = [];
                    try {
                        paramsDesc = (m.parameters ?? []).map((p: any) => {
                            try { return `${p.name}:${p.type?.name ?? "?"}`; } catch (_) { return "?"; }
                        });
                    } catch (_) { paramsDesc = ["<err>"]; }
                    let retType = "?";
                    try { retType = m.returnType?.name ?? "?"; } catch (_) { /* */ }
                    send({
                        type: "asm_crypto_method",
                        asm: asmName,
                        klass: kName,
                        name: mName,
                        isGeneric: !!m.isGeneric,
                        isInflated: !!m.isInflated,
                        isStatic: !!m.isStatic,
                        params: paramsDesc,
                        returnType: retType,
                        va,
                    });
                    asmMatches++;
                    totalMatches++;
                    if (totalMatches > 800) break;
                }
                if (totalMatches > 800) break;
            }
            send({ type: "asm_scan_result", asm: asmName, matches: asmMatches });
            if (totalMatches > 800) break;
        }
        send({ type: "asm_scan_phase", phase: "done", totalAsm, scannedAsm, totalMatches });
    }, "main");
}

/**
 * Enumerate every class in a specific assembly, emitting one `asm_class` event
 * with fullName, methodCount, fieldCount per class. Used to explore the
 * structure of assemblies we didn't inspect before (Cute.Http.Assembly,
 * umamusume.Http, Plugins, LibNative.Runtime).
 */
export function enumerateAssemblyClasses(assemblyName: string): void {
    send({ type: "asm_cls_phase", phase: "start", asm: assemblyName });
    Il2Cpp.perform(() => {
        let asm: any;
        try { asm = Il2Cpp.domain.assembly(assemblyName); } catch (e: any) {
            send({ type: "asm_cls_err", step: "assembly", asm: assemblyName, err: String(e?.message ?? e) });
            return;
        }
        let classes: any[] = [];
        try { classes = asm.image?.classes ?? []; } catch (_) { /* */ }
        let emitted = 0;
        for (const k of classes) {
            let fullName = "?";
            let methodCount = -1;
            try { fullName = k.fullName ?? k.name ?? "?"; } catch (_) { /* */ }
            try { methodCount = (k.methods ?? []).length; } catch (_) { /* */ }
            send({ type: "asm_class", asm: assemblyName, fullName, methodCount });
            emitted++;
            if (emitted > 2000) break;
        }
        send({ type: "asm_cls_phase", phase: "done", asm: assemblyName, emitted });
    }, "main");
}

/**
 * Enumerate a specific IL2CPP class by full name: walk up the base-class chain
 * and emit every method on every ancestor. Lets us discover shared base classes
 * (e.g. `Gallop.*Task` classes all share 7 methods → same base class holds the
 * actual Send/Serialize/Compress/Encrypt logic).
 */
export function enumerateClassWithAncestors(fullName: string): void {
    send({ type: "cls_enum_phase", phase: "start", fullName });
    Il2Cpp.perform(() => {
        const hit = findClassAnyAssembly(fullName);
        if (!hit) {
            send({ type: "cls_enum_err", step: "class_missing", fullName });
            return;
        }
        let klass: any = hit.klass;
        let depth = 0;
        while (klass && depth < 12) {
            let kName = "?";
            try { kName = klass.fullName ?? klass.name ?? "?"; } catch (_) { /* */ }
            let methods: any[] = [];
            try { methods = klass.methods ?? []; } catch (_) { /* */ }
            send({
                type: "cls_level",
                depth,
                klass: kName,
                methodCount: methods.length,
            });
            for (const m of methods) {
                let mName = "?";
                let va = "?";
                let paramsDesc: string[] = [];
                let retType = "?";
                try { mName = m.name ?? "?"; } catch (_) { /* */ }
                try { va = m.virtualAddress.toString(); } catch (_) { /* */ }
                try {
                    paramsDesc = (m.parameters ?? []).map((p: any) => {
                        try { return `${p.name}:${p.type?.name ?? "?"}`; } catch (_) { return "?"; }
                    });
                } catch (_) { paramsDesc = ["<err>"]; }
                try { retType = m.returnType?.name ?? "?"; } catch (_) { /* */ }
                send({
                    type: "cls_method",
                    depth,
                    klass: kName,
                    name: mName,
                    isGeneric: !!m.isGeneric,
                    isInflated: !!m.isInflated,
                    isStatic: !!m.isStatic,
                    params: paramsDesc,
                    returnType: retType,
                    va,
                });
            }
            let parent: any = null;
            try { parent = klass.parent ?? null; } catch (_) { parent = null; }
            if (!parent) break;
            klass = parent;
            depth++;
        }
        send({ type: "cls_enum_phase", phase: "done", fullName, depth });
    }, "main");
}

export function enumerateGallopHttpHelper(): void {
    send({ type: "gallop_phase", phase: "enum_start" });
    Il2Cpp.perform(() => {
        for (const klassName of GALLOP_CANDIDATE_CLASSES) {
            const hit = findClassAnyAssembly(klassName);
            if (!hit) {
                send({ type: "gallop_class_missing", klass: klassName });
                continue;
            }
            const { klass, asm } = hit;
            const methods = klass.methods ?? [];
            send({
                type: "gallop_class_found",
                klass: klassName,
                asm,
                fullName: klass.fullName,
                methodCount: methods.length,
            });
            for (const m of methods) {
                let va = "?";
                try { va = m.virtualAddress.toString(); } catch (_) { /* */ }
                let paramsDesc: string[] = [];
                try {
                    paramsDesc = m.parameters.map((p: any) => {
                        try { return `${p.name}:${p.type?.name ?? "?"}`; } catch (_) { return "?"; }
                    });
                } catch (_) { paramsDesc = ["<err>"]; }
                let retType = "?";
                try { retType = m.returnType?.name ?? "?"; } catch (_) { /* */ }
                send({
                    type: "gallop_method",
                    klass: klassName,
                    name: m.name,
                    isGeneric: !!m.isGeneric,
                    isInflated: !!m.isInflated,
                    isStatic: !!m.isStatic,
                    params: paramsDesc,
                    returnType: retType,
                    va,
                });
            }
        }
        send({ type: "gallop_phase", phase: "enum_done" });
    }, "main");
}

/**
 * LibNative.LZ4 discovery — Cygames' custom C# wrapper around their native
 * LZ4 plugin. The `LibNative.Runtime` assembly contains these classes:
 *   - LibNative.LZ4.Plugin — P/Invoke declarations (extern static) that BLR
 *     into libnative.so's LZ4_decompress_safe_ext / LZ4_compress_default_ext
 *   - LibNative.LZ4.SimpleLZ4Frame — higher-level frame codec; DecompressLZ4Bytes
 *     takes a byte[] frame and returns plaintext byte[]. Prime hook target.
 *   - LibNative.LZ4.StreamedLZ4FrameDecoder / StreamedLZ4Util — stream variants.
 *
 * Why hook here (libil2cpp.so) and NOT libnative.so:
 *   1. Shield path #3 hashes libnative.so export prologue bytes. Interceptor.attach
 *      on LZ4_decompress_safe_ext at skip=0 and skip=0x20 both trip it.
 *   2. Interceptor.attach in libil2cpp.so (shield path #2, a .text hash) has
 *      historically been tolerated — verified by 4-minute survival of the
 *      task_deserialize_intercept probe with 397 attached VAs.
 *   3. Stalker was the alternative but verified DEAD in this gadget (2026-04-21).
 *
 * This function ONLY enumerates — no hooks installed. Dump class/method list
 * with VAs so we pick the right overload.
 */
export function enumerateLibNativeLz4(): void {
    send({ type: "libnative_lz4_phase", phase: "enum_start" });
    Il2Cpp.perform(() => {
        // Try both plausible assembly names. Cygames might ship it as
        // "LibNative.Runtime" or just "LibNative".
        const tryAssemblies = ["LibNative.Runtime", "LibNative", "Assembly-CSharp"];
        let image: any = null;
        let chosen = "";
        for (const name of tryAssemblies) {
            try {
                image = Il2Cpp.domain.assembly(name).image;
                chosen = name;
                break;
            } catch (_) { /* next */ }
        }
        if (!image) {
            // Last resort: scan all assemblies for a class in LibNative.LZ4 namespace.
            let assemblies: any[] = [];
            try { assemblies = Il2Cpp.domain.assemblies ?? []; } catch (_) { /* */ }
            for (const a of assemblies) {
                let img: any = null;
                try { img = a.image; } catch (_) { continue; }
                let classes: any[] = [];
                try { classes = img.classes ?? []; } catch (_) { continue; }
                for (const k of classes) {
                    let kn = "";
                    try { kn = k.fullName ?? ""; } catch (_) { /* */ }
                    if (kn.startsWith("LibNative.LZ4.")) {
                        image = img;
                        chosen = a.name ?? "<unknown>";
                        break;
                    }
                }
                if (image) break;
            }
        }
        if (!image) {
            send({ type: "libnative_lz4_err", step: "no_assembly_found" });
            return;
        }
        send({ type: "libnative_lz4_image", assembly: chosen });

        let classes: any[] = [];
        try { classes = image.classes ?? []; } catch (_) { /* */ }
        let emitted = 0;
        for (const k of classes) {
            let kName = "";
            try { kName = k.fullName ?? k.name ?? ""; } catch (_) { continue; }
            if (!kName.startsWith("LibNative.LZ4")) continue;
            let methods: any[] = [];
            try { methods = k.methods ?? []; } catch (_) { continue; }
            for (const m of methods) {
                let paramsDesc: string[] = [];
                try {
                    paramsDesc = (m.parameters ?? []).map((p: any) => {
                        try { return `${p.type?.name ?? "?"} ${p.name ?? "?"}`; } catch (_) { return "?"; }
                    });
                } catch (_) { /* */ }
                let retType = "?";
                try { retType = m.returnType?.name ?? "?"; } catch (_) { /* */ }
                let vaStr = "?";
                try { vaStr = m.virtualAddress?.toString() ?? "?"; } catch (_) { /* */ }
                send({
                    type: "libnative_lz4_method",
                    klass: kName,
                    name: m.name,
                    isStatic: !!m.isStatic,
                    isGeneric: !!m.isGeneric,
                    params: paramsDesc,
                    returnType: retType,
                    va: vaStr,
                });
                emitted++;
            }
        }
        send({ type: "libnative_lz4_phase", phase: "enum_done", methodsEmitted: emitted });
    }, "main");
}

/**
 * Definitive Interceptor.attach-on-libil2cpp sanity test.
 *
 * We've seen repeated 0-hit results on libil2cpp.so VA attaches (397-VA task
 * deserialize probe, 3-VA LibNative.LZ4 probe). Can't tell from that whether
 * (a) Interceptor.attach is silently no-oping on libil2cpp VAs, or (b) we
 * just picked wrong methods.
 *
 * Test: hook `System.Object.ToString` (always callable), then immediately
 * invoke `new System.Object().ToString()` from the same agent. If the hook
 * fires, Interceptor.attach works — method-choice is the issue and we pivot
 * hook surface. If it doesn't, attach is dead on libil2cpp VAs and we need
 * a completely different approach (e.g., method.implementation table swap).
 *
 * Also tries `method.implementation = fn` as a second leg — tells us if
 * the table-swap path works even when Interceptor.attach doesn't.
 */
let _il2cppAttachSanityBooted = false;
export function il2cppAttachSanity(): void {
    if (_il2cppAttachSanityBooted) {
        send({ type: "il2cpp_sanity_err", step: "already_booted" });
        return;
    }
    _il2cppAttachSanityBooted = true;
    send({ type: "il2cpp_sanity_phase", phase: "start" });
    Il2Cpp.perform(() => {
        // Pick a simple method on a universally-available class.
        let mscorlib: any;
        try { mscorlib = Il2Cpp.domain.assembly("mscorlib").image; } catch (e: any) {
            send({ type: "il2cpp_sanity_err", step: "mscorlib", err: String(e?.message ?? e) });
            return;
        }
        let objClass: any;
        try { objClass = mscorlib.class("System.Object"); } catch (e: any) {
            send({ type: "il2cpp_sanity_err", step: "object_class", err: String(e?.message ?? e) });
            return;
        }
        // Find a parameterless ToString method.
        const methods = objClass.methods ?? [];
        const target = methods.find((m: any) => m.name === "ToString" && (m.parameters?.length ?? 0) === 0);
        if (!target) {
            send({ type: "il2cpp_sanity_err", step: "find_tostring" });
            return;
        }
        const va = target.virtualAddress;
        send({
            type: "il2cpp_sanity_resolved",
            klass: "System.Object",
            method: "ToString",
            va: va.toString(),
            isStatic: !!target.isStatic,
        });

        // LEG 1: Interceptor.attach.
        let interceptorHits = 0;
        try {
            Interceptor.attach(va, {
                onEnter() { interceptorHits++; },
            });
            send({ type: "il2cpp_sanity_attach", result: "ok" });
        } catch (e: any) {
            send({ type: "il2cpp_sanity_attach", result: "err", err: String(e?.message ?? e) });
        }

        // LEG 2: method.implementation swap.
        let implHits = 0;
        const originalImpl = target.implementation;
        try {
            target.implementation = function (this: any) {
                implHits++;
                // Delegate to original to keep semantics intact.
                try { return (originalImpl as any).call(this); } catch (_) { return null; }
            };
            send({ type: "il2cpp_sanity_impl", result: "ok" });
        } catch (e: any) {
            send({ type: "il2cpp_sanity_impl", result: "err", err: String(e?.message ?? e) });
        }

        // Self-invoke: call ToString on a fresh Object from within the agent.
        // If either leg works, one of the counters above ticks up.
        let selfCallOk = false;
        let selfCallResult = "";
        try {
            const inst = objClass.new();
            const r = inst.method("ToString").invoke();
            selfCallOk = true;
            try { selfCallResult = String(r); } catch (_) { selfCallResult = "<unstringifiable>"; }
        } catch (e: any) {
            send({ type: "il2cpp_sanity_err", step: "self_invoke", err: String(e?.message ?? e) });
        }

        // Brief delay then read counters. setImmediate-style via setTimeout(0).
        setTimeout(() => {
            send({
                type: "il2cpp_sanity_result",
                interceptorHits,
                implHits,
                selfCallOk,
                selfCallResult,
                verdict:
                    interceptorHits > 0 && implHits > 0 ? "BOTH_WORK" :
                    interceptorHits > 0 ? "ONLY_INTERCEPTOR_WORKS" :
                    implHits > 0 ? "ONLY_IMPL_SWAP_WORKS" :
                    "NEITHER_WORKS__CRACKPROOF_BLOCKS_IL2CPP_HOOKS",
            });
        }, 50);
    }, "main");
}

/**
 * Interceptor.attach on every LibNative.LZ4.* method whose name matches
 * /Decompress/ AND takes/returns byte[]. On enter: snapshot first N bytes of
 * the byte[] arg (the LZ4 frame). On leave: if return is a byte[], snapshot
 * the first N bytes (plaintext msgpack head).
 *
 * IL2CPP byte[] layout on arm64:
 *   offset 0x00: klass*
 *   offset 0x08: monitor*
 *   offset 0x10: bounds (or zero for 1D)
 *   offset 0x18: length (u32)
 *   offset 0x20: data...
 * For STATIC methods: args[0] = first user param (the byte[] ptr).
 * For INSTANCE methods: args[0] = this, args[1] = first user param.
 * Return value: same byte[] object pointer in x0.
 */
let _libNativeLz4HookBooted = false;
export function installLibNativeLz4Hooks(opts?: { maxSnapshot?: number }): void {
    if (_libNativeLz4HookBooted) {
        send({ type: "libnative_lz4_hook_err", step: "already_booted" });
        return;
    }
    _libNativeLz4HookBooted = true;
    const maxSnap = Math.max(16, Math.min(256, opts?.maxSnapshot ?? 64));

    send({ type: "libnative_lz4_phase", phase: "hook_start", maxSnap });
    Il2Cpp.perform(() => {
        const tryAssemblies = ["LibNative.Runtime", "LibNative", "Assembly-CSharp"];
        let image: any = null;
        for (const name of tryAssemblies) {
            try { image = Il2Cpp.domain.assembly(name).image; break; } catch (_) { /* */ }
        }
        if (!image) {
            // Scan for LibNative.LZ4.* namespace in any assembly.
            let assemblies: any[] = [];
            try { assemblies = Il2Cpp.domain.assemblies ?? []; } catch (_) { /* */ }
            outer: for (const a of assemblies) {
                let img: any = null;
                try { img = a.image; } catch (_) { continue; }
                let classes: any[] = [];
                try { classes = img.classes ?? []; } catch (_) { continue; }
                for (const k of classes) {
                    let kn = "";
                    try { kn = k.fullName ?? ""; } catch (_) { /* */ }
                    if (kn.startsWith("LibNative.LZ4.")) { image = img; break outer; }
                }
            }
        }
        if (!image) {
            send({ type: "libnative_lz4_hook_err", step: "no_assembly" });
            return;
        }

        type Entry = {
            va: NativePointer;
            klass: string;
            method: string;
            isStatic: boolean;
            kind: "byteArray" | "intPtr";
            byteArrayArgIdx: number;  // index into args[] (-1 if kind !== byteArray)
            returnsByteArray: boolean;
            // For intPtr kind: layout src/dst/size args so onEnter can read them.
            srcArgIdx: number;
            dstArgIdx: number;
            srcSizeArgIdx: number;
            dstCapArgIdx: number;
        };
        const entries: Entry[] = [];
        let classes: any[] = [];
        try { classes = image.classes ?? []; } catch (_) { /* */ }
        for (const k of classes) {
            let kName = "";
            try { kName = k.fullName ?? ""; } catch (_) { continue; }
            if (!kName.startsWith("LibNative.LZ4")) continue;
            let methods: any[] = [];
            try { methods = k.methods ?? []; } catch (_) { continue; }
            for (const m of methods) {
                if (m.isGeneric) continue;
                let params: any[] = [];
                try { params = m.parameters ?? []; } catch (_) { continue; }
                let va: NativePointer;
                try { va = m.virtualAddress; } catch (_) { continue; }
                if (va.isNull()) continue;
                const isStatic = !!m.isStatic;
                const argsSlotOffset = isStatic ? 0 : 1;

                // Kind A: byte[]-in (high-level SimpleLZ4Frame / StreamedLZ4Util).
                if (/Decompress|Compress/i.test(m.name)) {
                    let byteArgIdx = -1;
                    for (let i = 0; i < params.length; i++) {
                        try {
                            if ((params[i].type?.name ?? "") === "System.Byte[]") { byteArgIdx = i; break; }
                        } catch (_) { /* */ }
                    }
                    let returnsByteArray = false;
                    try { returnsByteArray = (m.returnType?.name ?? "") === "System.Byte[]"; } catch (_) { /* */ }
                    if (byteArgIdx >= 0) {
                        entries.push({
                            va,
                            klass: kName,
                            method: m.name,
                            isStatic,
                            kind: "byteArray",
                            byteArrayArgIdx: byteArgIdx + argsSlotOffset,
                            returnsByteArray,
                            srcArgIdx: -1, dstArgIdx: -1, srcSizeArgIdx: -1, dstCapArgIdx: -1,
                        });
                        continue;
                    }
                }

                // Kind B: Plugin P/Invoke stubs — LZ4_decompress_safe_ext etc.
                // Signature: (IntPtr src, IntPtr dst, Int32 srcSize, Int32 dstCap) → Int32
                // Hooking these catches every managed caller before the BLR into
                // libnative.so — if no managed code calls them, decompression is
                // pure-native (shield-protected territory).
                if (kName === "LibNative.LZ4.Plugin" && /LZ4_decompress_safe_ext|LZ4_compress_default_ext|LZ4_decompress_safe_continue/.test(m.name)) {
                    // Find argument indices by type name.
                    const paramTypes: string[] = [];
                    for (const p of params) {
                        try { paramTypes.push(p.type?.name ?? ""); } catch (_) { paramTypes.push(""); }
                    }
                    // Determine layout: last two Int32 = srcSize, dstCap; first two IntPtr = src, dst.
                    let srcIdx = -1, dstIdx = -1, srcSizeIdx = -1, dstCapIdx = -1;
                    for (let i = 0; i < paramTypes.length; i++) {
                        if (paramTypes[i] === "System.IntPtr") {
                            if (srcIdx < 0) srcIdx = i;
                            else if (dstIdx < 0) dstIdx = i;
                        } else if (paramTypes[i] === "System.Int32") {
                            if (srcSizeIdx < 0) srcSizeIdx = i;
                            else if (dstCapIdx < 0) dstCapIdx = i;
                        }
                    }
                    entries.push({
                        va,
                        klass: kName,
                        method: m.name,
                        isStatic,
                        kind: "intPtr",
                        byteArrayArgIdx: -1,
                        returnsByteArray: false,
                        srcArgIdx: srcIdx >= 0 ? srcIdx + argsSlotOffset : -1,
                        dstArgIdx: dstIdx >= 0 ? dstIdx + argsSlotOffset : -1,
                        srcSizeArgIdx: srcSizeIdx >= 0 ? srcSizeIdx + argsSlotOffset : -1,
                        dstCapArgIdx: dstCapIdx >= 0 ? dstCapIdx + argsSlotOffset : -1,
                    });
                }
            }
        }

        send({
            type: "libnative_lz4_hook_resolved",
            count: entries.length,
            sample: entries.slice(0, 10).map((e) => ({
                klass: e.klass, method: e.method, va: e.va.toString(),
                isStatic: e.isStatic, kind: e.kind,
                byteArgIdx: e.byteArrayArgIdx, returnsByteArray: e.returnsByteArray,
                srcArgIdx: e.srcArgIdx, dstArgIdx: e.dstArgIdx, srcSizeArgIdx: e.srcSizeArgIdx, dstCapArgIdx: e.dstCapArgIdx,
            })),
        });

        if (entries.length === 0) {
            send({ type: "libnative_lz4_hook_err", step: "no_candidates" });
            return;
        }

        const readByteArrayHead = (arrPtr: NativePointer): { len: number; head: string } => {
            if (arrPtr.isNull()) return { len: -1, head: "" };
            let len = -1;
            try { len = arrPtr.add(0x18).readU32(); } catch (_) { return { len: -1, head: "" }; }
            const n = Math.max(0, Math.min(len, maxSnap));
            if (n === 0) return { len, head: "" };
            let head = "";
            try {
                const bytes = arrPtr.add(0x20).readByteArray(n);
                if (bytes) {
                    const u8 = new Uint8Array(bytes);
                    for (let i = 0; i < u8.length; i++) {
                        const b = u8[i].toString(16);
                        head += b.length === 1 ? "0" + b : b;
                    }
                }
            } catch (_) { /* */ }
            return { len, head };
        };

        const perMethodCounts = new Map<string, number>();
        let totalHits = 0;
        let attachOk = 0;
        let attachErr = 0;
        for (const e of entries) {
            const tag = `${e.klass}|${e.method}`;
            try {
                Interceptor.attach(e.va, {
                    onEnter(args) {
                        totalHits++;
                        perMethodCounts.set(tag, (perMethodCounts.get(tag) ?? 0) + 1);
                        const nth = perMethodCounts.get(tag)!;
                        if (nth > 5) return;
                        (this as any)._libnativeLz4Tag = tag;
                        (this as any)._libnativeLz4Seq = nth;
                        if (e.kind === "byteArray") {
                            const arrPtr = args[e.byteArrayArgIdx];
                            const { len, head } = readByteArrayHead(arrPtr);
                            send({ type: "libnative_lz4_hit", tag, seq: nth, kind: "byteArray", inLen: len, inHead: head });
                        } else {
                            // IntPtr kind — read src/dst pointers + sizes, snapshot src bytes.
                            let srcPtr: NativePointer | null = null;
                            let srcSize = -1, dstCap = -1;
                            try { if (e.srcArgIdx >= 0) srcPtr = args[e.srcArgIdx] as NativePointer; } catch (_) { /* */ }
                            try { if (e.srcSizeArgIdx >= 0) srcSize = (args[e.srcSizeArgIdx] as NativePointer).toInt32(); } catch (_) { /* */ }
                            try { if (e.dstCapArgIdx >= 0) dstCap = (args[e.dstCapArgIdx] as NativePointer).toInt32(); } catch (_) { /* */ }
                            let srcHead = "";
                            if (srcPtr && !srcPtr.isNull() && srcSize > 0) {
                                const n = Math.min(srcSize, maxSnap);
                                try {
                                    const bytes = srcPtr.readByteArray(n);
                                    if (bytes) {
                                        const u8 = new Uint8Array(bytes);
                                        for (let i = 0; i < u8.length; i++) {
                                            const b = u8[i].toString(16);
                                            srcHead += b.length === 1 ? "0" + b : b;
                                        }
                                    }
                                } catch (_) { /* */ }
                            }
                            // Stash dst ptr so onLeave can snapshot the plaintext.
                            try { (this as any)._libnativeLz4Dst = e.dstArgIdx >= 0 ? args[e.dstArgIdx] : null; } catch (_) { (this as any)._libnativeLz4Dst = null; }
                            send({
                                type: "libnative_lz4_hit",
                                tag, seq: nth, kind: "intPtr",
                                inLen: srcSize, inHead: srcHead, dstCap,
                            });
                        }
                    },
                    onLeave(retval) {
                        const tag2 = (this as any)._libnativeLz4Tag;
                        const seq = (this as any)._libnativeLz4Seq;
                        if (!tag2 || !seq || seq > 5) return;
                        if (e.kind === "byteArray" && e.returnsByteArray) {
                            const { len, head } = readByteArrayHead(retval as any);
                            send({ type: "libnative_lz4_out", tag: tag2, seq, kind: "byteArray", outLen: len, outHead: head });
                        } else if (e.kind === "intPtr") {
                            // retval is the Int32 decompressed size. Read that many bytes from dst.
                            let decompressedSize = -1;
                            try { decompressedSize = (retval as NativePointer).toInt32(); } catch (_) { /* */ }
                            let plaintextHead = "";
                            const dstPtr = (this as any)._libnativeLz4Dst as NativePointer | null;
                            if (dstPtr && !dstPtr.isNull() && decompressedSize > 0) {
                                const n = Math.min(decompressedSize, maxSnap);
                                try {
                                    const bytes = dstPtr.readByteArray(n);
                                    if (bytes) {
                                        const u8 = new Uint8Array(bytes);
                                        for (let i = 0; i < u8.length; i++) {
                                            const b = u8[i].toString(16);
                                            plaintextHead += b.length === 1 ? "0" + b : b;
                                        }
                                    }
                                } catch (_) { /* */ }
                            }
                            send({ type: "libnative_lz4_out", tag: tag2, seq, kind: "intPtr", outLen: decompressedSize, outHead: plaintextHead });
                        }
                    },
                });
                attachOk++;
            } catch (err: any) {
                attachErr++;
                send({ type: "libnative_lz4_attach_err", tag, err: String(err?.message ?? err) });
            }
        }
        send({ type: "libnative_lz4_hook_installed", attachOk, attachErr, totalMethods: entries.length });

        setInterval(() => {
            const top = Array.from(perMethodCounts.entries())
                .sort((a, b) => b[1] - a[1]).slice(0, 8)
                .map(([tag, n]) => ({ tag, n }));
            send({ type: "libnative_lz4_stats", totalHits, topMethods: top });
        }, 2000);
    }, "main");
}

/**
 * Step 5: hook `Cute.Http.HttpManager.set_DecompressFunc(Func<byte[], byte[]>)`
 * and `set_CompressFunc(...)` to capture the managed delegate slots at the
 * moment Uma installs them. When captured, read the delegate's method pointer
 * (managed VA of the real de/compress routine) and hook it with snapshots.
 *
 * Also, because the delegate may have already been installed by the time this
 * RPC fires, read `HttpManager.Instance.DecompressFunc` / `.CompressFunc`
 * directly via the getter and handle that case too.
 *
 * All hooks are on libil2cpp.so — Interceptor.attach known shield-safe there.
 */
let _cuteHttpBooted = false;
let _cuteHttpSeq = 0;
const _cuteHttpHooked = new Set<string>();
export function captureCuteHttpDelegates(maxSnap: number = 0): void {
    if (_cuteHttpBooted) {
        send({ type: "cute_http_err", step: "already_booted" });
        return;
    }
    _cuteHttpBooted = true;
    send({ type: "cute_http_phase", phase: "start", maxSnap });

    Il2Cpp.perform(() => {
        let asm: any;
        try { asm = Il2Cpp.domain.assembly("Cute.Http.Assembly"); } catch (e: any) {
            send({ type: "cute_http_err", step: "assembly", err: String(e?.message ?? e) });
            return;
        }
        let httpMgr: any;
        try { httpMgr = asm.image.class("Cute.Http.HttpManager"); } catch (e: any) {
            send({ type: "cute_http_err", step: "class", err: String(e?.message ?? e) });
            return;
        }

        const methods: any[] = (() => { try { return httpMgr.methods ?? []; } catch (_) { return []; } })();
        const setDecomp = methods.find((m: any) => m.name === "set_DecompressFunc");
        const setComp = methods.find((m: any) => m.name === "set_CompressFunc");
        const getInst = methods.find((m: any) => m.name === "get_Instance");
        const getDecomp = methods.find((m: any) => m.name === "get_DecompressFunc");
        const getComp = methods.find((m: any) => m.name === "get_CompressFunc");

        send({
            type: "cute_http_resolved",
            setDecompVA: setDecomp ? setDecomp.virtualAddress.toString() : null,
            setCompVA: setComp ? setComp.virtualAddress.toString() : null,
            getInstVA: getInst ? getInst.virtualAddress.toString() : null,
            getDecompVA: getDecomp ? getDecomp.virtualAddress.toString() : null,
            getCompVA: getComp ? getComp.virtualAddress.toString() : null,
        });

        // Helper: read a Func<byte[],byte[]> delegate pointer's method_ptr and hook it.
        function introspectAndHook(delegatePtr: NativePointer, slot: string): void {
            if (!delegatePtr || delegatePtr.isNull()) {
                send({ type: "cute_http_delegate", slot, status: "null" });
                return;
            }
            let methodPtr: NativePointer | null = null;
            let methodFieldPtr: NativePointer | null = null;
            let targetPtr: NativePointer | null = null;
            try {
                // IL2CPP Il2CppDelegate layout (Il2CppObject header then fields):
                //   +0x00 Il2CppObject (klass, monitor)     = 16 bytes on arm64
                //   +0x10 Il2CppMethodPointer method_ptr    = fn pointer
                //   +0x18 MethodInfo* method
                //   +0x20 Il2CppObject* m_target
                //   +0x28 invoke_impl etc
                methodPtr = delegatePtr.add(0x10).readPointer();
                methodFieldPtr = delegatePtr.add(0x18).readPointer();
                targetPtr = delegatePtr.add(0x20).readPointer();
            } catch (e: any) {
                send({ type: "cute_http_err", slot, step: "read_delegate", err: String(e?.message ?? e) });
                return;
            }
            send({
                type: "cute_http_delegate",
                slot,
                status: "captured",
                delegatePtr: delegatePtr.toString(),
                methodPtr: methodPtr ? methodPtr.toString() : null,
                methodInfo: methodFieldPtr ? methodFieldPtr.toString() : null,
                target: targetPtr ? targetPtr.toString() : null,
            });

            if (!methodPtr || methodPtr.isNull()) return;
            const key = `${slot}@${methodPtr.toString()}`;
            if (_cuteHttpHooked.has(key)) return;
            _cuteHttpHooked.add(key);
            try {
                Interceptor.attach(methodPtr, {
                    onEnter(args) {
                        (this as any)._cuteSeq = ++_cuteHttpSeq;
                        try {
                            const arrPtr = args[0] as NativePointer;
                            // Il2CppArray<byte> layout:
                            //   +0x00 Il2CppObject (16 bytes)
                            //   +0x10 bounds*      (8 bytes)
                            //   +0x18 max_length   (uintptr)
                            //   +0x20 vector[0]    (raw byte data)
                            if (arrPtr.isNull()) {
                                send({ type: "cute_http_codec_in", slot, seq: (this as any)._cuteSeq, len: 0, sent: 0 });
                                return;
                            }
                            const len = Number(arrPtr.add(0x18).readU64().valueOf());
                            const n = maxSnap > 0 ? Math.min(len, maxSnap) : len;
                            const bytes = n > 0 ? arrPtr.add(0x20).readByteArray(n) : null;
                            send(
                                { type: "cute_http_codec_in", slot, seq: (this as any)._cuteSeq, len, sent: n },
                                bytes as any,
                            );
                        } catch (e: any) {
                            send({ type: "cute_http_err", slot, step: "read_in", err: String(e?.message ?? e) });
                        }
                    },
                    onLeave(retval) {
                        const seq = (this as any)._cuteSeq;
                        try {
                            const arrPtr = retval as NativePointer;
                            if (arrPtr.isNull()) {
                                send({ type: "cute_http_codec_out", slot, seq, len: 0, sent: 0 });
                                return;
                            }
                            const len = Number(arrPtr.add(0x18).readU64().valueOf());
                            const n = maxSnap > 0 ? Math.min(len, maxSnap) : len;
                            const bytes = n > 0 ? arrPtr.add(0x20).readByteArray(n) : null;
                            send(
                                { type: "cute_http_codec_out", slot, seq, len, sent: n },
                                bytes as any,
                            );
                        } catch (e: any) {
                            send({ type: "cute_http_err", slot, step: "read_out", err: String(e?.message ?? e) });
                        }
                    },
                });
                send({ type: "cute_http_codec_hooked", slot, methodPtr: methodPtr.toString() });
            } catch (e: any) {
                send({ type: "cute_http_err", slot, step: "attach_codec", err: String(e?.message ?? e) });
            }
        }

        // Leg 1: hook the setters so we catch re-installs / late installs.
        for (const [setter, slot] of [[setDecomp, "decompress"], [setComp, "compress"]] as any) {
            if (!setter) {
                send({ type: "cute_http_err", step: "setter_missing", slot });
                continue;
            }
            try {
                Interceptor.attach(setter.virtualAddress, {
                    onEnter(args) {
                        // instance method: args[0]=this, args[1]=value (Func delegate pointer)
                        try {
                            introspectAndHook(args[1] as NativePointer, slot);
                        } catch (e: any) {
                            send({ type: "cute_http_err", slot, step: "setter_onEnter", err: String(e?.message ?? e) });
                        }
                    },
                });
                send({ type: "cute_http_setter_hooked", slot, va: setter.virtualAddress.toString() });
            } catch (e: any) {
                send({ type: "cute_http_err", slot, step: "attach_setter", err: String(e?.message ?? e) });
            }
        }

        // Leg 2: call get_Instance().DecompressFunc / .CompressFunc now, in case
        // the delegate is already installed by the time this RPC fires.
        if (getInst && getDecomp && getComp) {
            try {
                const inst = httpMgr.method("get_Instance").invoke();
                if (inst && !inst.isNull?.()) {
                    try {
                        const d = inst.method("get_DecompressFunc").invoke();
                        const ptr = (d && d.handle) ? d.handle : (d as any);
                        introspectAndHook(ptr, "decompress_initial");
                    } catch (e: any) {
                        send({ type: "cute_http_err", step: "get_decomp", err: String(e?.message ?? e) });
                    }
                    try {
                        const c = inst.method("get_CompressFunc").invoke();
                        const ptr = (c && c.handle) ? c.handle : (c as any);
                        introspectAndHook(ptr, "compress_initial");
                    } catch (e: any) {
                        send({ type: "cute_http_err", step: "get_comp", err: String(e?.message ?? e) });
                    }
                } else {
                    send({ type: "cute_http_delegate", slot: "initial", status: "no_instance" });
                }
            } catch (e: any) {
                send({ type: "cute_http_err", step: "get_instance", err: String(e?.message ?? e) });
            }
        }

        send({ type: "cute_http_phase", phase: "installed" });
    }, "main");
}

