/**
 * SSL/TLS layer probe for Uma Musume Global.
 *
 * First pass — minimal, read-only. Install ONE Interceptor.attach on SSL_read
 * with only an onEnter logger (no writes, no argument mutation). If Uma dies
 * within 5s after hook install, we've learned libssl is also scanned and need
 * to pivot again.
 *
 * Signature (OpenSSL / BoringSSL):
 *   int SSL_read(SSL *ssl, void *buf, int num);
 * Returns:
 *   > 0 : bytes read (TLS plaintext)
 *   = 0 : connection closed cleanly
 *   < 0 : error (SSL_ERROR_WANT_READ common for non-blocking sockets)
 */

type ModuleInfo = {
    name: string;
    base: string;
    size: number;
    path: string;
};

const SSL_MODULE_REGEX = /ssl|boringssl|tls|crypto|chromium|cronet/i;
const SSL_SYMBOL_REGEX = /SSL_read|SSL_write|SSL_CTX|ssl3_|tls1_|BIO_read|BIO_write/;

function listSslModules(): any[] {
    return Process.enumerateModules().filter((m) => SSL_MODULE_REGEX.test(m.name));
}

/**
 * Emit 'ssl_module' events for every module whose name matches the SSL regex,
 * then for each matching module try to enumerate symbols (falling back to
 * exports) and emit 'ssl_symbol' events for SSL-related symbol names.
 * Finally emit 'ssl_enum_done'.
 */
export function enumerateSslModules(): void {
    send({ type: "ssl_enum_phase", phase: "start" });
    const mods = listSslModules();
    send({ type: "ssl_enum_summary", count: mods.length });
    for (const m of mods) {
        const info: ModuleInfo = {
            name: m.name,
            base: m.base.toString(),
            size: m.size,
            path: m.path,
        };
        send({ type: "ssl_module", ...info });

        let symbols: any[] = [];
        let source = "symbols";
        try {
            symbols = (m as any).enumerateSymbols?.() ?? [];
        } catch (_) {
            symbols = [];
        }
        if (!symbols || symbols.length === 0) {
            try {
                symbols = (m as any).enumerateExports?.() ?? [];
                source = "exports";
            } catch (_) {
                symbols = [];
            }
        }
        let emitted = 0;
        for (const s of symbols) {
            if (!s || !s.name) continue;
            if (!SSL_SYMBOL_REGEX.test(s.name)) continue;
            let addr = "?";
            try { addr = s.address.toString(); } catch (_) { /* */ }
            let offset = "?";
            try { offset = s.address.sub(m.base).toString(); } catch (_) { /* */ }
            send({
                type: "ssl_symbol",
                module: m.name,
                source,
                name: s.name,
                address: addr,
                offset,
                symbolType: s.type ?? "?",
            });
            emitted++;
            if (emitted >= 50) break;
        }
        send({ type: "ssl_module_symbols_done", module: m.name, emitted, source });
    }
    send({ type: "ssl_enum_done" });
}

/**
 * Resolve SSL_read across all loaded modules. Prefer the global export lookup
 * (works regardless of which module hosts it); fall back to iterating SSL-ish
 * modules and asking each one. Support both frida-gum 17 and 19 APIs.
 */
function resolveSslRead(): { addr: NativePointer | null; moduleName: string | null; searched: string[] } {
    const searched: string[] = [];

    // 1. Global export lookup — catches static/dynamic regardless of module.
    let addr: NativePointer | null = null;
    try {
        addr = (Module as any).getGlobalExportByName?.("SSL_read") ?? null;
    } catch (_) {
        addr = null;
    }
    if (!addr) {
        try {
            addr = (Module as any).findExportByName?.(null, "SSL_read") ?? null;
        } catch (_) {
            addr = null;
        }
    }
    if (addr) {
        // Try to identify which module it came from.
        let modName: string | null = null;
        try {
            const hostMod = Process.findModuleByAddress(addr);
            if (hostMod) modName = hostMod.name;
        } catch (_) { /* */ }
        return { addr, moduleName: modName, searched };
    }

    // 2. Iterate SSL-ish modules.
    const mods = listSslModules();
    for (const m of mods) {
        searched.push(m.name);
        let found: NativePointer | null = null;
        try {
            found = (m as any).findExportByName?.("SSL_read") ?? null;
        } catch (_) {
            found = null;
        }
        if (!found) {
            // Try symbol enumeration.
            try {
                const syms = (m as any).enumerateSymbols?.() ?? [];
                for (const s of syms) {
                    if (s && s.name === "SSL_read") {
                        found = s.address;
                        break;
                    }
                }
            } catch (_) { /* */ }
        }
        if (found) {
            return { addr: found, moduleName: m.name, searched };
        }
    }

    return { addr: null, moduleName: null, searched };
}

let _sslReadInstalled = false;

/**
 * Install a minimal read-only Interceptor.attach on SSL_read. No writes, no
 * argument mutation. First 10 calls emit detailed events; thereafter a 2s
 * stats ping emits hit count + last size. onLeave reads up to 64 bytes of
 * actual plaintext (retval bytes) for the first 10 calls only.
 */
export function installSslReadProbe(): { hooked: boolean; addr?: string; moduleName?: string } {
    if (_sslReadInstalled) {
        send({ type: "ssl_read_err", step: "already_installed" });
        return { hooked: true };
    }

    const { addr, moduleName, searched } = resolveSslRead();
    if (!addr) {
        send({ type: "ssl_read_not_found", searched });
        return { hooked: false };
    }

    const addrStr = addr.toString();
    const resolvedModule = moduleName ?? "unknown";
    send({ type: "ssl_read_resolved", addr: addrStr, module: resolvedModule });

    let hits = 0;
    let lastSize = -1;

    function toHex(buf: ArrayBuffer | null): string {
        if (!buf) return "";
        const u8 = new Uint8Array(buf);
        let s = "";
        for (let i = 0; i < u8.length; i++) {
            const b = u8[i].toString(16);
            s += b.length === 1 ? "0" + b : b;
        }
        return s;
    }

    try {
        Interceptor.attach(addr, {
            onEnter(args) {
                hits++;
                (this as any).seq = hits;
                const sslPtr = args[0];
                const bufPtr = args[1];
                let num = -1;
                try { num = args[2].toInt32(); } catch (_) { /* */ }
                (this as any).size = num;
                (this as any).buf = bufPtr;
                lastSize = num;
                if (hits <= 10) {
                    send({
                        type: "ssl_read_call",
                        seq: hits,
                        ssl: sslPtr.toString(),
                        buf: bufPtr.toString(),
                        num,
                        module: resolvedModule,
                        addr: addrStr,
                    });
                }
            },
            onLeave(retval) {
                const seq = (this as any).seq || 0;
                let ret = -0x80000000;
                try { ret = retval.toInt32(); } catch (_) { /* */ }
                let previewHex = "";
                const bufPtr: NativePointer = (this as any).buf;
                const size: number = (this as any).size;
                if (seq <= 10 && ret > 0 && size > 0 && ret <= size && bufPtr && !bufPtr.isNull()) {
                    try {
                        const n = Math.min(ret, 64);
                        const bytes = bufPtr.readByteArray(n);
                        previewHex = toHex(bytes);
                    } catch (_) {
                        previewHex = "<read_err>";
                    }
                }
                if (seq <= 10) {
                    send({
                        type: "ssl_read_return",
                        seq,
                        retval: ret,
                        preview: previewHex,
                        module: resolvedModule,
                        addr: addrStr,
                    });
                }
            },
        });
        _sslReadInstalled = true;
        send({ type: "ssl_read_installed", addr: addrStr, module: resolvedModule });
    } catch (e: any) {
        send({ type: "ssl_read_err", step: "attach", err: String(e?.message ?? e) });
        return { hooked: false };
    }

    // Heartbeat every 2s so we know the probe is alive, and to report hit count
    // past the detailed-event cutoff.
    setInterval(() => {
        send({
            type: "ssl_read_stats",
            hits,
            lastSize,
            moduleName: resolvedModule,
            addr: addrStr,
        });
    }, 2000);

    return { hooked: true, addr: addrStr, moduleName: resolvedModule };
}

// ---------------------------------------------------------------------------
// BoringSSL-specific probes: hook SSL_read AND SSL_write in libssl.so only.
//
// Why: the "global" SSL_read lookup tends to return the Conscrypt/libjavacrypto
// implementation (JNI wrapper for BoringSSL used by Java SSLEngine). Uma's
// OkHttp client uses its own bundled BoringSSL at libssl.so — different code,
// different address. We want plaintext for the game-api traffic, which rides
// on the libssl.so side.
// ---------------------------------------------------------------------------

let _boringSslInstalled = false;

function hexEncode(buf: ArrayBuffer | null): string {
    if (!buf) return "";
    const u8 = new Uint8Array(buf);
    let s = "";
    for (let i = 0; i < u8.length; i++) {
        const b = u8[i].toString(16);
        s += b.length === 1 ? "0" + b : b;
    }
    return s;
}

type BoringResolve = {
    readHook: boolean;
    writeHook: boolean;
    readAddr?: string;
    writeAddr?: string;
};

function findInModule(mod: any, nameRe: RegExp): { name: string; address: NativePointer } | null {
    // 1. direct export lookup.
    const tryNames = ["SSL_read", "SSL_write"];
    for (const n of tryNames) {
        if (!nameRe.test(n)) continue;
        try {
            const a = mod.findExportByName?.(n) ?? null;
            if (a) return { name: n, address: a };
        } catch (_) { /* */ }
    }
    // 2. enumerate symbols, pick by regex.
    try {
        const syms = mod.enumerateSymbols?.() ?? [];
        for (const s of syms) {
            if (!s || !s.name) continue;
            if (nameRe.test(s.name)) {
                return { name: s.name, address: s.address };
            }
        }
    } catch (_) { /* */ }
    // 3. enumerate exports, pick by regex (covers cases where export name is exotic).
    try {
        const exps = mod.enumerateExports?.() ?? [];
        for (const e of exps) {
            if (!e || !e.name) continue;
            if (nameRe.test(e.name)) {
                return { name: e.name, address: e.address };
            }
        }
    } catch (_) { /* */ }
    return null;
}

/**
 * Resolve + hook SSL_read and SSL_write in libssl.so specifically.
 * Read-only — no argument mutation, no return-value mutation.
 */
export function installBoringSslProbes(): BoringResolve {
    if (_boringSslInstalled) {
        send({ type: "boringssl_err", step: "already_installed" });
        return { readHook: true, writeHook: true };
    }

    const mod: any = Process.findModuleByName("libssl.so");
    if (!mod) {
        send({ type: "ssl_module_missing", module: "libssl.so" });
        return { readHook: false, writeHook: false };
    }
    const base = mod.base.toString();
    send({ type: "ssl_module_found", module: "libssl.so", base, size: mod.size, path: mod.path });

    // Primary: exact SSL_read / SSL_write.
    const readRe = /^SSL_read$|^SSL_read_internal$/;
    const writeRe = /^SSL_write$|^SSL_write_internal$/;

    let readHit = findInModule(mod, readRe);
    let writeHit = findInModule(mod, writeRe);

    // Fallback: mangled bssl::SSL_read / bssl::SSL_write.
    if (!readHit) {
        readHit = findInModule(mod, /SSL_read(Ex)?$|bssl.*SSL_?read/);
    }
    if (!writeHit) {
        writeHit = findInModule(mod, /SSL_write(Ex)?$|bssl.*SSL_?write/);
    }

    send({
        type: "ssl_hook_resolve",
        module: "libssl.so",
        base,
        read: readHit ? { name: readHit.name, addr: readHit.address.toString(), offset: readHit.address.sub(mod.base).toString() } : null,
        write: writeHit ? { name: writeHit.name, addr: writeHit.address.toString(), offset: writeHit.address.sub(mod.base).toString() } : null,
    });

    // If neither resolved, dump a diagnostic list of similar symbols so we can pivot.
    if (!readHit && !writeHit) {
        try {
            const syms = mod.enumerateSymbols?.() ?? [];
            const hits = [];
            for (const s of syms) {
                if (!s || !s.name) continue;
                if (/SSL_|ssl_read|ssl_write|bssl/i.test(s.name)) {
                    hits.push({ name: s.name, offset: s.address.sub(mod.base).toString() });
                    if (hits.length >= 40) break;
                }
            }
            send({ type: "ssl_symbol_dump", count: hits.length, sample: hits });
        } catch (e: any) {
            send({ type: "ssl_symbol_dump_err", err: String(e?.message ?? e) });
        }
    }

    let readHooked = false;
    let writeHooked = false;

    // -- SSL_read hook
    if (readHit) {
        let hits = 0;
        let minSize = Number.POSITIVE_INFINITY;
        let maxSize = 0;
        let sumSize = 0;
        let statCount = 0;
        try {
            Interceptor.attach(readHit.address, {
                onEnter(args) {
                    hits++;
                    (this as any).seq = hits;
                    (this as any).buf = args[1];
                    let num = -1;
                    try { num = args[2].toInt32(); } catch (_) { /* */ }
                    (this as any).size = num;
                },
                onLeave(retval) {
                    const seq = (this as any).seq || 0;
                    let ret = -0x80000000;
                    try { ret = retval.toInt32(); } catch (_) { /* */ }
                    const bufPtr: NativePointer = (this as any).buf;
                    const size: number = (this as any).size;
                    if (ret > 0) {
                        minSize = Math.min(minSize, ret);
                        maxSize = Math.max(maxSize, ret);
                        sumSize += ret;
                        statCount++;
                    }
                    if (seq <= 10 && ret > 0 && size > 0 && ret <= size && bufPtr && !bufPtr.isNull()) {
                        let head = "";
                        try {
                            const n = Math.min(ret, 128);
                            const bytes = bufPtr.readByteArray(n);
                            head = hexEncode(bytes);
                        } catch (_) {
                            head = "<read_err>";
                        }
                        send({
                            type: "boringssl_read_return",
                            seq,
                            size,
                            retval: ret,
                            head_hex: head,
                            addr: readHit!.address.toString(),
                            name: readHit!.name,
                        });
                    }
                },
            });
            readHooked = true;
            send({ type: "boringssl_read_installed", addr: readHit.address.toString(), name: readHit.name });
        } catch (e: any) {
            send({ type: "boringssl_read_err", step: "attach", err: String(e?.message ?? e) });
        }
        // Stats ping every 2s.
        setInterval(() => {
            const mean = statCount > 0 ? Math.round(sumSize / statCount) : 0;
            send({
                type: "boringssl_read_stats",
                hits,
                retpos: statCount,
                last_size_range: [Number.isFinite(minSize) ? minSize : 0, maxSize, mean],
            });
        }, 2000);
    }

    // -- SSL_write hook (read plaintext BEFORE encryption in onEnter)
    if (writeHit) {
        let hits = 0;
        let minSize = Number.POSITIVE_INFINITY;
        let maxSize = 0;
        let sumSize = 0;
        try {
            Interceptor.attach(writeHit.address, {
                onEnter(args) {
                    hits++;
                    const bufPtr = args[1];
                    let num = -1;
                    try { num = args[2].toInt32(); } catch (_) { /* */ }
                    if (num > 0) {
                        minSize = Math.min(minSize, num);
                        maxSize = Math.max(maxSize, num);
                        sumSize += num;
                    }
                    if (hits <= 10 && num > 0 && bufPtr && !bufPtr.isNull()) {
                        let head = "";
                        try {
                            const n = Math.min(num, 128);
                            const bytes = bufPtr.readByteArray(n);
                            head = hexEncode(bytes);
                        } catch (_) {
                            head = "<read_err>";
                        }
                        send({
                            type: "boringssl_write_call",
                            seq: hits,
                            size: num,
                            head_hex: head,
                            addr: writeHit!.address.toString(),
                            name: writeHit!.name,
                        });
                    }
                },
            });
            writeHooked = true;
            send({ type: "boringssl_write_installed", addr: writeHit.address.toString(), name: writeHit.name });
        } catch (e: any) {
            send({ type: "boringssl_write_err", step: "attach", err: String(e?.message ?? e) });
        }
        setInterval(() => {
            const mean = hits > 0 ? Math.round(sumSize / hits) : 0;
            send({
                type: "boringssl_write_stats",
                hits,
                last_size_range: [Number.isFinite(minSize) ? minSize : 0, maxSize, mean],
            });
        }, 2000);
    }

    _boringSslInstalled = readHooked || writeHooked;

    return {
        readHook: readHooked,
        writeHook: writeHooked,
        readAddr: readHit ? readHit.address.toString() : undefined,
        writeAddr: writeHit ? writeHit.address.toString() : undefined,
    };
}

// ---------------------------------------------------------------------------
// Conscrypt ENGINE_SSL_{read,write}_direct probes.
//
// Uma uses OkHttp -> Conscrypt SSLEngine path, not the direct C SSL_read/write.
// The "direct" variants take a raw plaintext buffer pointer (no BIO indirection),
// so extraction is straightforward.
//
// JNI signature (per OpenJDK/Conscrypt):
//   jint NativeCrypto_ENGINE_SSL_read_direct(
//       JNIEnv* env, jclass, jlong ssl_address, jobject ssl_holder,
//       jlong address, jint length, jobject shc)
// ARM64: x0=env, x1=jclass, x2=ssl_address, x3=ssl_holder,
//        x4=address (plaintext buffer), x5=length, x6=shc
// ---------------------------------------------------------------------------

let _conscryptEngineInstalled = false;

type ConscryptResolve = {
    readHook: boolean;
    writeHook: boolean;
    readAddr?: string;
    writeAddr?: string;
};

export function installConscryptEngineProbes(): ConscryptResolve {
    if (_conscryptEngineInstalled) {
        send({ type: "conscrypt_engine_err", step: "already_installed" });
        return { readHook: true, writeHook: true };
    }

    const mod: any = Process.findModuleByName("libjavacrypto.so");
    if (!mod) {
        send({ type: "conscrypt_module_missing", module: "libjavacrypto.so" });
        return { readHook: false, writeHook: false };
    }
    const base = mod.base.toString();
    send({
        type: "conscrypt_module_found",
        module: "libjavacrypto.so",
        base,
        size: mod.size,
        path: mod.path,
    });

    // Enumerate symbols and pick by substring match — don't rely on exact mangling.
    let syms: any[] = [];
    try {
        syms = mod.enumerateSymbols?.() ?? [];
    } catch (_) {
        syms = [];
    }

    let readSym: { name: string; address: NativePointer } | null = null;
    let writeSym: { name: string; address: NativePointer } | null = null;
    for (const s of syms) {
        if (!s || !s.name) continue;
        if (!readSym && s.name.indexOf("ENGINE_SSL_read_direct") >= 0) {
            readSym = { name: s.name, address: s.address };
        }
        if (!writeSym && s.name.indexOf("ENGINE_SSL_write_direct") >= 0) {
            writeSym = { name: s.name, address: s.address };
        }
        if (readSym && writeSym) break;
    }

    if (!readSym || !writeSym) {
        // Dump a sample of NativeCrypto_* symbols for debugging.
        const sample: Array<{ name: string; offset: string }> = [];
        for (const s of syms) {
            if (!s || !s.name) continue;
            if (s.name.indexOf("NativeCrypto") >= 0 || s.name.indexOf("ENGINE_SSL") >= 0) {
                try {
                    sample.push({ name: s.name, offset: s.address.sub(mod.base).toString() });
                } catch (_) {
                    sample.push({ name: s.name, offset: "?" });
                }
                if (sample.length >= 60) break;
            }
        }
        send({
            type: "conscrypt_engine_symbols_not_found",
            have_read: !!readSym,
            have_write: !!writeSym,
            sample_count: sample.length,
            sample,
        });
        return { readHook: false, writeHook: false };
    }

    send({
        type: "conscrypt_engine_resolved",
        read: { name: readSym.name, addr: readSym.address.toString(), offset: readSym.address.sub(mod.base).toString() },
        write: { name: writeSym.name, addr: writeSym.address.toString(), offset: writeSym.address.sub(mod.base).toString() },
    });

    let readHooked = false;
    let writeHooked = false;

    // --- ENGINE_SSL_read_direct: buf filled on RETURN, valid bytes = retval
    {
        let hits = 0;
        let retPosCount = 0;
        let retMin = Number.POSITIVE_INFINITY;
        let retMax = 0;
        let retSum = 0;
        try {
            Interceptor.attach(readSym.address, {
                onEnter(args) {
                    hits++;
                    (this as any).seq = hits;
                    (this as any).buf = args[4];
                    let size = -1;
                    try { size = args[5].toInt32(); } catch (_) { /* */ }
                    (this as any).size = size;
                },
                onLeave(retval) {
                    const seq = (this as any).seq || 0;
                    let ret = -0x80000000;
                    try { ret = retval.toInt32(); } catch (_) { /* */ }
                    const bufPtr: NativePointer = (this as any).buf;
                    const size: number = (this as any).size;
                    if (ret > 0) {
                        retPosCount++;
                        retMin = Math.min(retMin, ret);
                        retMax = Math.max(retMax, ret);
                        retSum += ret;
                    }
                    if (seq <= 10 && ret > 0 && size > 0 && ret <= size && bufPtr && !bufPtr.isNull()) {
                        let head = "";
                        try {
                            const n = Math.min(ret, 128);
                            const bytes = bufPtr.readByteArray(n);
                            head = hexEncode(bytes);
                        } catch (_) {
                            head = "<read_err>";
                        }
                        send({
                            type: "engine_ssl_read",
                            seq,
                            size,
                            retval: ret,
                            head_hex: head,
                        });
                    }
                },
            });
            readHooked = true;
            send({ type: "engine_ssl_read_installed", addr: readSym.address.toString(), name: readSym.name });
        } catch (e: any) {
            send({ type: "engine_ssl_read_err", step: "attach", err: String(e?.message ?? e) });
        }
        setInterval(() => {
            const mean = retPosCount > 0 ? Math.round(retSum / retPosCount) : 0;
            send({
                type: "engine_ssl_read_stats",
                hits,
                retval_pos: retPosCount,
                retval_min: Number.isFinite(retMin) ? retMin : 0,
                retval_max: retMax,
                retval_mean: mean,
            });
        }, 2000);
    }

    // --- ENGINE_SSL_write_direct: buf filled on ENTRY, plaintext = length bytes
    {
        let hits = 0;
        let sizeMin = Number.POSITIVE_INFINITY;
        let sizeMax = 0;
        let sizeSum = 0;
        try {
            Interceptor.attach(writeSym.address, {
                onEnter(args) {
                    hits++;
                    const bufPtr = args[4];
                    let size = -1;
                    try { size = args[5].toInt32(); } catch (_) { /* */ }
                    if (size > 0) {
                        sizeMin = Math.min(sizeMin, size);
                        sizeMax = Math.max(sizeMax, size);
                        sizeSum += size;
                    }
                    if (hits <= 10 && size > 0 && bufPtr && !bufPtr.isNull()) {
                        let head = "";
                        try {
                            const n = Math.min(size, 128);
                            const bytes = bufPtr.readByteArray(n);
                            head = hexEncode(bytes);
                        } catch (_) {
                            head = "<read_err>";
                        }
                        send({
                            type: "engine_ssl_write",
                            seq: hits,
                            size,
                            head_hex: head,
                        });
                    }
                },
            });
            writeHooked = true;
            send({ type: "engine_ssl_write_installed", addr: writeSym.address.toString(), name: writeSym.name });
        } catch (e: any) {
            send({ type: "engine_ssl_write_err", step: "attach", err: String(e?.message ?? e) });
        }
        setInterval(() => {
            const mean = hits > 0 ? Math.round(sizeSum / hits) : 0;
            send({
                type: "engine_ssl_write_stats",
                hits,
                size_min: Number.isFinite(sizeMin) ? sizeMin : 0,
                size_max: sizeMax,
                size_mean: mean,
            });
        }, 2000);
    }

    _conscryptEngineInstalled = readHooked || writeHooked;

    return {
        readHook: readHooked,
        writeHook: writeHooked,
        readAddr: readSym.address.toString(),
        writeAddr: writeSym.address.toString(),
    };
}

// ---------------------------------------------------------------------------
// Wide-net SSL scan + hook: walk every module, find symbols matching any of
// the common SSL/TLS/crypto function name patterns, and either just report
// (scanAllSslSymbols) or install lightweight Interceptor.attach on each
// unique address (installAllSslHooks).
//
// The shield forbids us from hooking inside libil2cpp.so. Matches there are
// reported and then SKIPPED.
// ---------------------------------------------------------------------------

const WIDE_SSL_RE = new RegExp(
    "^(SSL_read|SSL_write|SSL_read_ex|SSL_write_ex|BIO_read|BIO_write|" +
    "SSL_do_handshake|NativeCrypto_.*SSL_.*read|NativeCrypto_.*SSL_.*write|" +
    "_ZN.*SSL_read|_ZN.*SSL_write|_ZN.*bssl.*SSL)$"
);
const WIDE_SSL_LOOSE_RE = /ssl_read|ssl_write/i;
const IL2CPP_MODULE = "libil2cpp.so";

type SslSymHit = {
    module: string;
    name: string;
    address: NativePointer;
    offset: string;
};

// Modules we MUST NOT iterate symbols on. libil2cpp enumeration causes a
// clean exit(0) in ~2-3s (shield detects it). Packed shield loader modules
// (lib__XXXX__.so) are also dangerous. Unity/native are large + anonymous-
// adjacent; skip defensively.
const SYM_ENUM_SKIP_RE = /^libil2cpp\.so$|^libnative\.so$|^libunity\.so$|^libmain\.so$|^lib__[0-9a-f]+__\.so$|^lib_[0-9a-f]+_\.so$|^\[|^$/i;
// We only enumerate symbols on modules whose NAME hints at SSL/TLS/crypto/net.
const SYM_ENUM_ALLOW_RE = /ssl|tls|crypt|boring|cronet|net\.so|chromium|conscrypt|openssl|quic|curl/i;

function collectSslSymbols(): SslSymHit[] {
    const out: SslSymHit[] = [];
    const mods = Process.enumerateModules();
    const visited: Array<{ name: string; size: number; reason: string }> = [];
    for (const m of mods) {
        if (SYM_ENUM_SKIP_RE.test(m.name)) {
            visited.push({ name: m.name, size: m.size, reason: "skip" });
            continue;
        }
        if (!SYM_ENUM_ALLOW_RE.test(m.name)) {
            continue;
        }
        visited.push({ name: m.name, size: m.size, reason: "scan" });
        let syms: any[] = [];
        try {
            syms = (m as any).enumerateSymbols?.() ?? [];
        } catch (_) {
            syms = [];
        }
        if (!syms || syms.length === 0) {
            try {
                syms = (m as any).enumerateExports?.() ?? [];
            } catch (_) {
                syms = [];
            }
        }
        for (const s of syms) {
            if (!s || !s.name || !s.address) continue;
            const name: string = s.name;
            if (!(WIDE_SSL_RE.test(name) || WIDE_SSL_LOOSE_RE.test(name))) continue;
            let off = "?";
            try { off = s.address.sub(m.base).toString(); } catch (_) { /* */ }
            out.push({
                module: m.name,
                name,
                address: s.address,
                offset: off,
            });
        }
    }
    send({ type: "ssl_sym_enum_modules", visited });
    return out;
}

/**
 * Walk every loaded module; report every symbol matching the SSL regex.
 * Emits `ssl_symbol_scan` events (capped at 200) and `ssl_symbol_scan_done`.
 */
export function scanAllSslSymbols(): void {
    send({ type: "ssl_symbol_scan_start" });
    const hits = collectSslSymbols();
    const byModule: Record<string, number> = {};
    let emitted = 0;
    for (const h of hits) {
        byModule[h.module] = (byModule[h.module] ?? 0) + 1;
        if (emitted < 200) {
            send({
                type: "ssl_symbol_scan",
                module: h.module,
                name: h.name,
                address: h.address.toString(),
                offset: h.offset,
            });
            emitted++;
        }
    }
    send({
        type: "ssl_symbol_scan_done",
        total: hits.length,
        emitted,
        byModule,
    });
}

let _allSslHooksInstalled = false;

/**
 * Install an Interceptor.attach on every unique address produced by
 * collectSslSymbols(). Matches inside libil2cpp.so are SKIPPED (shield).
 * Returns summary of installed hooks.
 */
export function installAllSslHooks(): { total: number; hooks: Array<{ module: string; symbol: string; addr: string }> } {
    if (_allSslHooksInstalled) {
        send({ type: "all_ssl_err", step: "already_installed" });
        return { total: 0, hooks: [] };
    }
    const hits = collectSslSymbols();
    // dedupe by address
    const seen = new Set<string>();
    const unique: SslSymHit[] = [];
    for (const h of hits) {
        const a = h.address.toString();
        if (seen.has(a)) continue;
        seen.add(a);
        unique.push(h);
    }

    send({ type: "all_ssl_scan_summary", total_matches: hits.length, unique_addrs: unique.length });

    const installed: Array<{ module: string; symbol: string; addr: string }> = [];
    const detailedPerAddr: Record<string, number> = {};
    const hitsPerAddr: Record<string, number> = {};
    let globalSeq = 0;
    for (const h of unique) {
        const addrStr = h.address.toString();
        if (h.module === IL2CPP_MODULE) {
            send({
                type: "ssl_il2cpp_match_skipped",
                module: h.module,
                symbol: h.name,
                address: addrStr,
                offset: h.offset,
            });
            continue;
        }
        const isWrite = /write/i.test(h.name);
        const isRead = /read/i.test(h.name);
        const modName = h.module;
        const symName = h.name;
        try {
            Interceptor.attach(h.address, {
                onEnter(args) {
                    let a0 = "?", a1 = "?", a2 = "?", a3 = "?";
                    try { a0 = args[0].toString(); } catch (_) { /* */ }
                    try { a1 = args[1].toString(); } catch (_) { /* */ }
                    try { a2 = args[2].toString(); } catch (_) { /* */ }
                    try { a3 = args[3].toString(); } catch (_) { /* */ }
                    (this as any).a0 = a0;
                    (this as any).a1 = a1;
                    (this as any).a2 = a2;
                    (this as any).a3 = a3;
                    (this as any)._writeHead = "";
                    if (isWrite) {
                        try {
                            const bufPtr = args[1];
                            if (bufPtr && !bufPtr.isNull()) {
                                const head = bufPtr.readByteArray(64);
                                if (head) {
                                    (this as any)._writeHead = hexEncode(head);
                                }
                            }
                        } catch (_) { /* */ }
                    }
                    (this as any)._readBuf = isRead ? args[1] : null;
                },
                onLeave(retval) {
                    globalSeq++;
                    hitsPerAddr[addrStr] = (hitsPerAddr[addrStr] ?? 0) + 1;
                    let ret = -0x80000000;
                    try { ret = retval.toInt32(); } catch (_) { /* */ }
                    const count = detailedPerAddr[addrStr] ?? 0;
                    if (count < 10) {
                        let head = "";
                        if (isRead && ret > 0) {
                            const buf: NativePointer = (this as any)._readBuf;
                            if (buf && !buf.isNull()) {
                                try {
                                    const n = Math.min(ret, 64);
                                    const b = buf.readByteArray(n);
                                    if (b) head = hexEncode(b);
                                } catch (_) { head = "<rerr>"; }
                            }
                        } else if (isWrite) {
                            head = (this as any)._writeHead || "";
                        }
                        detailedPerAddr[addrStr] = count + 1;
                        send({
                            type: "any_ssl_hit",
                            seq: globalSeq,
                            module: modName,
                            symbol: symName,
                            a0: (this as any).a0,
                            a1: (this as any).a1,
                            a2: (this as any).a2,
                            a3: (this as any).a3,
                            retval: ret,
                            head_hex: head,
                        });
                    }
                },
            });
            installed.push({ module: modName, symbol: symName, addr: addrStr });
        } catch (e: any) {
            send({ type: "all_ssl_err", step: "attach", module: modName, symbol: symName, addr: addrStr, err: String(e?.message ?? e) });
        }
    }

    _allSslHooksInstalled = true;

    // Stats ping every 2s.
    setInterval(() => {
        const nonZero: Array<{ addr: string; hits: number }> = [];
        for (const k of Object.keys(hitsPerAddr)) {
            if (hitsPerAddr[k] > 0) nonZero.push({ addr: k, hits: hitsPerAddr[k] });
        }
        nonZero.sort((a, b) => b.hits - a.hits);
        send({
            type: "any_ssl_stats",
            installed: installed.length,
            total_hits: globalSeq,
            top: nonZero.slice(0, 10),
        });
    }, 2000);

    send({
        type: "all_ssl_installed",
        total: installed.length,
        hooks: installed.slice(0, 40),
    });

    return { total: installed.length, hooks: installed };
}

// ---------------------------------------------------------------------------
// Fixed-name SSL hooks via Module.findGlobalExportByName. Safer than walking
// `enumerateSymbols` across all modules (which hits PROT_NONE guards inside
// libil2cpp/libnative/unpacked shield regions). The dynamic linker resolves
// these names via its hash table — ART/libc do this constantly, shield can't
// flag it.
// ---------------------------------------------------------------------------

const FIXED_SSL_NAMES = [
    "SSL_read",
    "SSL_write",
    "SSL_read_ex",
    "SSL_write_ex",
    "BIO_read",
    "BIO_write",
    "SSL_do_handshake",
];

let _fixedSslHooksInstalled = false;

export function installFixedSslHooks(): { total: number; hooks: Array<{ symbol: string; addr: string }> } {
    if (_fixedSslHooksInstalled) {
        send({ type: "fixed_ssl_err", step: "already_installed" });
        return { total: 0, hooks: [] };
    }

    const resolved: Array<{ symbol: string; addr: NativePointer }> = [];
    for (const name of FIXED_SSL_NAMES) {
        let addr: NativePointer | null = null;
        try {
            addr = (Module as any).getGlobalExportByName?.(name) ?? null;
        } catch (_) {
            addr = null;
        }
        if (!addr) {
            try {
                addr = (Module as any).findExportByName?.(null, name) ?? null;
            } catch (_) {
                addr = null;
            }
        }
        if (addr && !addr.isNull()) {
            resolved.push({ symbol: name, addr });
            send({ type: "fixed_ssl_resolved", symbol: name, addr: addr.toString() });
        } else {
            send({ type: "fixed_ssl_missing", symbol: name });
        }
    }

    const installed: Array<{ symbol: string; addr: string }> = [];
    const hitsPerSym: Record<string, number> = {};
    const detailedPerSym: Record<string, number> = {};
    let seq = 0;

    for (const r of resolved) {
        const sym = r.symbol;
        const addrStr = r.addr.toString();
        const isWrite = /write/i.test(sym);
        const isRead = /read/i.test(sym);
        try {
            Interceptor.attach(r.addr, {
                onEnter(args) {
                    (this as any)._sym = sym;
                    (this as any)._readBuf = isRead ? args[1] : null;
                    (this as any)._writeHead = "";
                    if (isWrite) {
                        try {
                            const bufPtr = args[1];
                            if (bufPtr && !bufPtr.isNull()) {
                                const head = bufPtr.readByteArray(64);
                                if (head) (this as any)._writeHead = hexEncode(head);
                            }
                        } catch (_) { /* */ }
                    }
                },
                onLeave(retval) {
                    seq++;
                    hitsPerSym[sym] = (hitsPerSym[sym] ?? 0) + 1;
                    let ret = -0x80000000;
                    try { ret = retval.toInt32(); } catch (_) { /* */ }
                    const count = detailedPerSym[sym] ?? 0;
                    if (count < 10) {
                        let head = "";
                        if (isRead && ret > 0) {
                            const buf: NativePointer = (this as any)._readBuf;
                            if (buf && !buf.isNull()) {
                                try {
                                    const n = Math.min(ret, 64);
                                    const b = buf.readByteArray(n);
                                    if (b) head = hexEncode(b);
                                } catch (_) { head = "<rerr>"; }
                            }
                        } else if (isWrite) {
                            head = (this as any)._writeHead || "";
                        }
                        detailedPerSym[sym] = count + 1;
                        send({
                            type: "fixed_ssl_hit",
                            seq,
                            symbol: sym,
                            retval: ret,
                            head_hex: head,
                        });
                    }
                },
            });
            installed.push({ symbol: sym, addr: addrStr });
        } catch (e: any) {
            send({ type: "fixed_ssl_err", step: "attach", symbol: sym, addr: addrStr, err: String(e?.message ?? e) });
        }
    }

    _fixedSslHooksInstalled = true;

    setInterval(() => {
        send({
            type: "fixed_ssl_stats",
            installed: installed.length,
            total_hits: seq,
            per_sym: hitsPerSym,
        });
    }, 2000);

    send({
        type: "fixed_ssl_installed",
        total: installed.length,
        hooks: installed,
    });

    return { total: installed.length, hooks: installed };
}
