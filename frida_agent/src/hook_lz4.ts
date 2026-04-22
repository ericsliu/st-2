/**
 * LZ4 hook for Uma Musume.
 *
 * libnative.so exports four LZ4 symbols (Cygames' custom build):
 *   LZ4_decompress_safe_ext        — prime target; API response decompression
 *   LZ4_compress_default_ext
 *   LZ4_init
 *   LZ4_decompress_safe_continue
 *
 * Signature probe: we start with the standard LZ4_decompress_safe shape
 * (const char* src, char* dst, int srcSize, int dstCap) → returns int. The
 * _ext variant may take an extra key/seed arg; we log first 6 args and look
 * at argv[4..5] to see if they look like pointers, ints, or zero.
 *
 * On each call we:
 *  - record src/dst pointers and sizes
 *  - on return, if retval > 0 we snapshot the first N bytes of dst as hex
 *    (so the host can look at the plaintext msgpack)
 */

import { enumerateFollowTargets, enumerateBroadFollowTargets } from "./hook_deserializer";

type Lz4Call = {
    type: "lz4_call";
    seq: number;
    srcSize: number;
    dstCap: number;
    arg4: string;
    arg5: string;
    srcHead: string;
    retval: number;
    plaintextHead: string;
    plaintextLen: number;
};

let seq = 0;
let installed = false;

function toHex(addr: NativePointer, len: number): string {
    try {
        const bytes = addr.readByteArray(len);
        if (!bytes) return "";
        const view = new Uint8Array(bytes);
        let s = "";
        for (let i = 0; i < view.length; i++) {
            s += view[i].toString(16).padStart(2, "0");
        }
        return s;
    } catch (_) {
        return "<unreadable>";
    }
}

function getExport(mod: string, sym: string): NativePointer | null {
    try {
        const m = Process.findModuleByName(mod);
        if (!m) return null;
        const addr = (m as any).findExportByName?.(sym) ?? null;
        if (addr) return addr;
    } catch (_) {
        /* fallthrough */
    }
    try {
        const addr = (Module as any).findExportByName?.(null, sym) ?? null;
        return addr;
    } catch (_) {
        return null;
    }
}

export function installLz4Hook(opts?: { maxSnapshot?: number; prologueSkip?: number }): boolean {
    if (installed) return true;
    const maxSnap = opts?.maxSnapshot ?? 64;
    const skip = opts?.prologueSkip ?? 0;

    const exportAddr = getExport("libnative.so", "LZ4_decompress_safe_ext");
    if (!exportAddr) {
        send({ type: "lz4_hook", status: "target_not_found" });
        return false;
    }
    const target = skip > 0 ? exportAddr.add(skip) : exportAddr;
    send({ type: "lz4_hook", status: "resolved", exportAddr: exportAddr.toString(), hookAddr: target.toString(), prologueSkip: skip });

    Interceptor.attach(target, {
        onEnter(args) {
            this.seq = ++seq;
            this.src = args[0];
            this.dst = args[1];
            this.srcSize = args[2].toInt32();
            this.dstCap = args[3].toInt32();
            this.arg4 = args[4].toString();
            this.arg5 = args[5].toString();
            this.srcHead = toHex(args[0], Math.min(this.srcSize, 32));
        },
        onLeave(retval) {
            const rv = retval.toInt32();
            const plaintextLen = rv > 0 ? Math.min(rv, maxSnap) : 0;
            const plaintextHead = plaintextLen > 0 ? toHex(this.dst, plaintextLen) : "";
            const msg: Lz4Call = {
                type: "lz4_call",
                seq: this.seq,
                srcSize: this.srcSize,
                dstCap: this.dstCap,
                arg4: this.arg4,
                arg5: this.arg5,
                srcHead: this.srcHead,
                retval: rv,
                plaintextHead,
                plaintextLen: rv > 0 ? rv : 0,
            };
            send(msg);
        },
    });

    send({ type: "lz4_hook", status: "installed", target: target.toString() });
    installed = true;
    return true;
}

/**
 * Stalker-based variant. Does NOT patch libnative.so.
 *
 * Strategy:
 *   1. Resolve LZ4_decompress_safe_ext export from libnative.so.
 *   2. Stalker.exclude the whole libnative.so range — when a stalked thread
 *      calls into libnative, Stalker falls back to native (unstalked)
 *      execution. No internal LZ4 recompilation overhead, and crucially
 *      no modification of libnative.so bytes.
 *   3. Stalker.follow on the UnityMain / IL2CPP Threadpool / main-pid
 *      threads with a transform that inspects every instruction at compile
 *      time. When we see `bl <LZ4_target>` — a direct branch-and-link
 *      whose immediate operand equals our resolved export — we
 *      iterator.putCallout right BEFORE it, then iterator.keep().
 *   4. The callout runs with x0..x3 already set up per AAPCS64: x0=src,
 *      x1=dst, x2=srcSize, x3=dstCap. Sample the first 32 bytes of src
 *      (compressed LZ4 frame) and aggregate srcSize histogram.
 *
 * Why this evades shield path #3:
 *   - No bytes in libnative.so are modified. A prologue-hash integrity
 *     check against LZ4_decompress_safe_ext reads the original bytes.
 *   - Only the IL2CPP/Unity *caller* side is recompiled, and only into
 *     a private Stalker JIT heap — the original libil2cpp.so .text is
 *     also untouched (so shield path #2 also stays happy).
 *
 * Caveat: indirect calls (BLR x8 from a vtable) would be missed. P/Invoke
 * stubs that IL2CPP emits for native-method calls are direct BL, so this
 * should work for the HTTP response decompression path.
 */
let _nativeLz4StalkerBooted = false;
export function probeStalkerOnNativeLz4(excludeLibnative: boolean = true, broadFollow: boolean = false): void {
    if (_nativeLz4StalkerBooted) {
        send({ type: "native_lz4_stalker_err", step: "already_booted" });
        return;
    }
    _nativeLz4StalkerBooted = true;

    send({ type: "stalker_phase", phase: "native_lz4_start" });

    const mod = Process.findModuleByName("libnative.so");
    if (!mod) {
        send({ type: "native_lz4_stalker_err", step: "libnative_missing" });
        return;
    }

    let target: NativePointer | null = null;
    try { target = (mod as any).findExportByName?.("LZ4_decompress_safe_ext") ?? null; } catch (_) { target = null; }
    if (!target) {
        try { target = (Module as any).findExportByName?.(null, "LZ4_decompress_safe_ext") ?? null; } catch (_) { target = null; }
    }
    if (!target) {
        send({ type: "native_lz4_stalker_err", step: "export_missing" });
        return;
    }
    const targetStr = target.toString();
    send({
        type: "native_lz4_stalker_resolved",
        target: targetStr,
        libnativeBase: mod.base.toString(),
        libnativeSize: mod.size,
        offset: target.sub(mod.base).toString(),
    });

    if (excludeLibnative) {
        try {
            Stalker.exclude({ base: mod.base, size: mod.size });
            send({ type: "native_lz4_stalker_excluded", base: mod.base.toString(), size: mod.size });
        } catch (e: any) {
            send({ type: "native_lz4_stalker_err", step: "exclude", err: String(e?.message ?? e) });
        }
    } else {
        send({ type: "native_lz4_stalker_not_excluded" });
    }

    let hits = 0;
    const srcLenCounts: Record<number, number> = {};
    const dstCapCounts: Record<number, number> = {};

    // Diagnostic: track every block.start Stalker compiles inside libnative.
    // Tells us which threads actually enter libnative code and whether the
    // target VA appears. Rate-limited — we care about unique offsets, not
    // per-block spam.
    const libnativeBase = mod.base;
    const libnativeEnd = mod.base.add(mod.size);
    const libnativeBlockOffsets = new Set<string>();
    const libnativeThreadHits: Record<string, number> = {};
    let libnativeTotalBlockCompiles = 0;
    // Universal block counter — proves Stalker is actually instrumenting
    // anything. If this stays 0, Stalker.follow is silently failing.
    let totalAnyBlockCompiles = 0;
    const anyBlockThreadHits: Record<string, number> = {};
    // Sample of library names where blocks compile — tells us where the
    // followed threads actually execute code.
    const libsSeen: Record<string, number> = {};

    function callout(context: any): void {
        hits++;
        const seq = hits;
        const src = context.x0;
        const dst = context.x1;
        let srcSize = -1;
        let dstCap = -1;
        try { srcSize = context.x2.toInt32(); } catch (_) { /* */ }
        try { dstCap = context.x3.toInt32(); } catch (_) { /* */ }

        if (srcSize >= 0) srcLenCounts[srcSize] = (srcLenCounts[srcSize] || 0) + 1;
        if (dstCap >= 0) dstCapCounts[dstCap] = (dstCapCounts[dstCap] || 0) + 1;

        if (seq <= 10) {
            let srcHead = "";
            try {
                const n = Math.max(0, Math.min(32, srcSize));
                if (n > 0 && !src.isNull()) {
                    const bytes = src.readByteArray(n);
                    if (bytes) {
                        const u8 = new Uint8Array(bytes);
                        for (let i = 0; i < u8.length; i++) {
                            const b = u8[i].toString(16);
                            srcHead += b.length === 1 ? "0" + b : b;
                        }
                    }
                }
            } catch (_) { /* ignore */ }
            send({
                type: "native_lz4_hit",
                seq,
                target: targetStr,
                src: src.toString(),
                dst: dst.toString(),
                srcSize,
                dstCap,
                srcHead,
            });
        }
    }

    // Transform: fires on arrival AT LZ4 entry (block.start == target) OR on
    // direct `bl <target>` in the caller. Entry match catches indirect calls
    // (IL2CPP P/Invoke stubs typically dispatch via `blr x8` through a dlsym
    // table, which `bl` filtering would miss). Requires libnative NOT be
    // Stalker-excluded, otherwise block.start will never equal target.
    //
    // Diagnostic side-channel: we also note every block.start whose address
    // falls within libnative.so's range, to confirm which threads actually
    // execute libnative code at all.
    const transform = (iterator: any) => {
        let ins: any;
        let first = true;
        while ((ins = iterator.next()) !== null) {
            try {
                if (first) {
                    const a = ins.address;
                    totalAnyBlockCompiles++;
                    const tidKey = String(Process.getCurrentThreadId());
                    anyBlockThreadHits[tidKey] = (anyBlockThreadHits[tidKey] || 0) + 1;
                    // Bucket which library this block lives in. Cheap heuristic:
                    // ask Process for the module by address (cache per-address
                    // would be nicer but this runs only once per new block).
                    if (totalAnyBlockCompiles <= 5000) {
                        try {
                            const m = Process.findModuleByAddress(a);
                            const k = m?.name ?? "<anon>";
                            libsSeen[k] = (libsSeen[k] || 0) + 1;
                        } catch (_) { /* */ }
                    }
                    if (a.compare(libnativeBase) >= 0 && a.compare(libnativeEnd) < 0) {
                        libnativeTotalBlockCompiles++;
                        const off = a.sub(libnativeBase).toString();
                        libnativeBlockOffsets.add(off);
                        libnativeThreadHits[tidKey] = (libnativeThreadHits[tidKey] || 0) + 1;
                    }
                    if (a.equals(target!)) {
                        iterator.putCallout(callout);
                    }
                } else if (ins.mnemonic === "bl") {
                    const ops = ins.operands;
                    if (ops && ops.length > 0 && ops[0].type === "imm") {
                        const callTarget = ptr(String(ops[0].value));
                        if (callTarget.equals(target!)) {
                            iterator.putCallout(callout);
                        }
                    }
                }
            } catch (_) {
                /* keep going */
            }
            first = false;
            iterator.keep();
        }
    };

    const enumerate = broadFollow ? enumerateBroadFollowTargets : enumerateFollowTargets;
    send({ type: "native_lz4_stalker_mode", broadFollow, excludeLibnative });
    const initialTargets = enumerate();
    const followedTids = new Set<number>();
    let followed = 0;
    for (const t of initialTargets) {
        const tid = t.tid;
        try { Stalker.unfollow(tid); } catch (_) { /* */ }
        try {
            Stalker.follow(tid, { transform });
            followed++;
            followedTids.add(tid);
        } catch (e: any) {
            send({ type: "native_lz4_follow_err", tid, name: t.comm, err: String(e?.message ?? e) });
        }
    }
    send({ type: "native_lz4_stalker_followed", followed, target: targetStr });

    // Thread re-sweep: HTTP worker threads in libnative.so (libcurl's internal
    // thread, mbedTLS) may be spawned on first API call AFTER our initial
    // follow pass. Re-scan every 1s and follow new threads that match the
    // whitelist — also try catch-all for unknown-named native threads.
    setInterval(() => {
        const fresh = enumerate();
        for (const t of fresh) {
            if (followedTids.has(t.tid)) continue;
            try { Stalker.unfollow(t.tid); } catch (_) { /* */ }
            try {
                Stalker.follow(t.tid, { transform });
                followedTids.add(t.tid);
                send({ type: "native_lz4_new_follow", tid: t.tid, comm: t.comm });
            } catch (e: any) {
                send({ type: "native_lz4_new_follow_err", tid: t.tid, comm: t.comm, err: String(e?.message ?? e) });
            }
        }
    }, 1000);

    // Stats heartbeat.
    setInterval(() => {
        const topSrc = Object.keys(srcLenCounts)
            .map((k) => [parseInt(k, 10), srcLenCounts[parseInt(k, 10)]] as [number, number])
            .sort((a, b) => b[1] - a[1]).slice(0, 5)
            .map(([len, n]) => ({ len, n }));
        const topDst = Object.keys(dstCapCounts)
            .map((k) => [parseInt(k, 10), dstCapCounts[parseInt(k, 10)]] as [number, number])
            .sort((a, b) => b[1] - a[1]).slice(0, 5)
            .map(([len, n]) => ({ len, n }));
        // Sample up to 8 libnative block offsets to show where execution lives.
        const offsetSample: string[] = [];
        let n = 0;
        for (const off of libnativeBlockOffsets) {
            offsetSample.push(off);
            if (++n >= 8) break;
        }
        const threadTop: Array<{ tid: number; blocks: number }> = Object.keys(libnativeThreadHits)
            .map((k) => ({ tid: parseInt(k, 10), blocks: libnativeThreadHits[k] }))
            .sort((a, b) => b.blocks - a.blocks)
            .slice(0, 6);
        const libsTop = Object.keys(libsSeen)
            .map((k) => ({ lib: k, n: libsSeen[k] }))
            .sort((a, b) => b.n - a.n)
            .slice(0, 10);
        const anyThreadTop: Array<{ tid: number; blocks: number }> = Object.keys(anyBlockThreadHits)
            .map((k) => ({ tid: parseInt(k, 10), blocks: anyBlockThreadHits[k] }))
            .sort((a, b) => b.blocks - a.blocks)
            .slice(0, 6);
        send({
            type: "native_lz4_stalker_stats",
            hits,
            topSrcLens: topSrc,
            topDstCaps: topDst,
            uniqueSrcLens: Object.keys(srcLenCounts).length,
            libnativeUniqueBlockOffsets: libnativeBlockOffsets.size,
            libnativeTotalCompiles: libnativeTotalBlockCompiles,
            libnativeOffsetSample: offsetSample,
            libnativeThreadTop: threadTop,
            anyBlockCompiles: totalAnyBlockCompiles,
            anyThreadTop,
            libsSeen: libsTop,
        });
    }, 2000);
}

/**
 * Minimal Stalker health check.
 *
 * If `probeStalkerOnNativeLz4` reports `anyBlockCompiles: 0` across hundreds
 * of followed threads for minutes of wall-clock traffic, Stalker itself is
 * not instrumenting anything. This probe tests the absolute simplest case:
 * Stalker.follow() the *current* (RPC) thread with a no-op transform that
 * just increments a counter per block compiled. If the counter stays at 0,
 * Stalker is broken in this gadget env (ZygiskFrida / CrackProof
 * interference). If it ticks up, Stalker works and the `lz4-stalker` probe's
 * zero-hit finding means follow() on *other* threads is silently failing.
 */
export function probeStalkerHealth(durationMs: number = 3000): void {
    send({ type: "stalker_health_start", durationMs });
    let compiled = 0;
    let callouts = 0;
    const transform = (iterator: any) => {
        let ins: any;
        let first = true;
        while ((ins = iterator.next()) !== null) {
            if (first) {
                compiled++;
                iterator.putCallout(() => { callouts++; });
            }
            first = false;
            iterator.keep();
        }
    };
    const tid = Process.getCurrentThreadId();
    try {
        Stalker.follow(tid, { transform });
        send({ type: "stalker_health_followed", tid });
    } catch (e: any) {
        send({ type: "stalker_health_err", step: "follow", err: String(e?.message ?? e) });
        return;
    }
    // Heartbeat: let JS event loop pump `setInterval` while we also do busy
    // work in a separate phase. We try BOTH patterns so we can see whether
    // Stalker's compile is tied to JS execution or the underlying native
    // thread.
    const start = Date.now();
    let spins = 0;
    const heartbeat = setInterval(() => {
        send({ type: "stalker_health_hb", elapsedMs: Date.now() - start, compiled, callouts });
    }, 500);
    // Busy-loop phase — blocks JS event loop; Frida's script thread is native,
    // and Stalker.follow(selfTid) means the transform SHOULD fire on whatever
    // code Frida's JS runtime is executing to drive this function.
    while (Date.now() - start < durationMs) {
        for (let i = 0; i < 1000; i++) spins = (spins + i) | 0;
    }
    clearInterval(heartbeat);
    try { Stalker.unfollow(tid); } catch (_) { /* */ }
    // Flush Stalker's queued blocks (some frida-gum versions buffer until
    // Stalker.flush() — without it the transform callbacks may never run).
    try { Stalker.flush(); } catch (_) { /* */ }
    send({ type: "stalker_health_done", tid, compiledBlocks: compiled, callouts, spins });
}

/**
 * Stalker health via the `events` API instead of `transform`. Some frida-gum
 * versions treat transform callbacks differently from event callbacks. If
 * this reports events > 0 while transform reports 0, it's a transform-API
 * issue; if both are 0, Stalker is dead at the root.
 */
export function probeStalkerHealthEvents(durationMs: number = 3000): void {
    send({ type: "stalker_health_events_start", durationMs });
    let eventBatches = 0;
    let totalEvents = 0;
    const tid = Process.getCurrentThreadId();
    try {
        Stalker.follow(tid, {
            events: { call: true, compile: true },
            onReceive: (events: ArrayBuffer) => {
                eventBatches++;
                totalEvents += events.byteLength;
            },
        } as any);
        send({ type: "stalker_health_events_followed", tid });
    } catch (e: any) {
        send({ type: "stalker_health_events_err", step: "follow", err: String(e?.message ?? e) });
        return;
    }
    const start = Date.now();
    let spins = 0;
    const heartbeat = setInterval(() => {
        send({ type: "stalker_health_events_hb", elapsedMs: Date.now() - start, eventBatches, totalEvents });
    }, 500);
    while (Date.now() - start < durationMs) {
        for (let i = 0; i < 1000; i++) spins = (spins + i) | 0;
    }
    clearInterval(heartbeat);
    try { Stalker.unfollow(tid); } catch (_) { /* */ }
    try { Stalker.flush(); } catch (_) { /* */ }
    send({ type: "stalker_health_events_done", tid, eventBatches, totalEvents, spins });
}
