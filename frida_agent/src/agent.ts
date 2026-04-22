/**
 * Uma Musume Frida agent.
 *
 * WS-2 scaffold: loads cleanly into the game process, emits a 'ready' event,
 * exposes rpc.exports for module enumeration and LZ4 probing, watches for
 * interesting module loads via dlopen hook, installs anti-debug bypass.
 *
 * WS-4 will extend this with the LZ4_decompress_safe hook (see hook_lz4.ts).
 */

import { installPtraceBypass } from "./antidebug";
import { installLz4Hook, probeStalkerOnNativeLz4, probeStalkerHealth, probeStalkerHealthEvents } from "./hook_lz4";
import { installExitTraps } from "./trap_exit";
import { installJavaExitTraps } from "./trap_java_exit";
import {
    discoverDeserializers,
    installDeserializerHooks,
    hookGenericDispatchStub,
    probeStalkerFollow,
    probeStalkerOnGenericDispatch,
    probeStalkerTransformOnDispatch,
    probeStalkerOnLz4Decode,
    probeStalkerOnLz4Codec,
    probeStalkerTransformOnLz4Codec,
    probeStalkerTransformOnMpReadBytes,
    findNonGenericMethodCandidates,
    enumerateGallopHttpHelper,
    probeStalkerTransformOnGallopHttp,
    scanGallopCompressionMethods,
    probeStalkerTransformOnCryptAes,
    enumerateAllAssemblies,
    scanAllAssembliesForCrypto,
    enumerateAssemblyClasses,
    enumerateClassWithAncestors,
    probeStalkerTransformOnTaskDeserialize,
    interceptAttachOnTaskDeserialize,
    enumerateLibNativeLz4,
    installLibNativeLz4Hooks,
    il2cppAttachSanity,
    captureCuteHttpDelegates,
} from "./hook_deserializer";
import { enumerateSslModules, installSslReadProbe, installBoringSslProbes, installConscryptEngineProbes, scanAllSslSymbols, installAllSslHooks, installFixedSslHooks } from "./hook_ssl";

type ModuleInfo = {
    name: string;
    base: string;
    size: number;
    path: string;
};

const WATCH_MODULES = /libnative\.so|libmain\.so|libil2cpp\.so|libunity\.so/;

function snapshotModules(filterRegex?: RegExp): ModuleInfo[] {
    return Process.enumerateModules()
        .filter((m) => (filterRegex ? filterRegex.test(m.name) : true))
        .map((m) => ({
            name: m.name,
            base: m.base.toString(),
            size: m.size,
            path: m.path,
        }));
}

function enumerateLz4Candidates(moduleName: string = "libnative.so"): Array<{ name: string; address: string }> {
    const mod = Process.findModuleByName(moduleName);
    if (!mod) {
        return [];
    }
    return mod
        .enumerateExports()
        .filter((e) => /LZ4|decompress/i.test(e.name))
        .map((e) => ({ name: e.name, address: e.address.toString() }));
}

type StringHit = { module: string; address: string; offset: string; text: string };

/**
 * Scan the given module's read-only memory for ASCII/UTF-8 strings matching
 * any pattern in `needles`. Returns all hits as { module, address, offset, text }.
 *
 * This is how we find statically-linked LZ4: scan libil2cpp/libunity/libnative
 * for strings like "LZ4", "LZ4HC", "lz4", "Error decompressing" — these will
 * sit in .rodata near the decompress function. Cross-reference with nearby
 * function symbols to identify the LZ4_decompress_safe entry point.
 */
function asciiToHexPattern(s: string): string {
    const out: string[] = [];
    for (let i = 0; i < s.length; i++) {
        out.push(s.charCodeAt(i).toString(16).padStart(2, "0"));
    }
    return out.join(" ");
}

function scanStrings(moduleName: string, needles: string[]): StringHit[] {
    const mod = Process.findModuleByName(moduleName);
    if (!mod) return [];
    const hits: StringHit[] = [];
    // Scan BOTH r-- (normal rodata) AND r-x (code + embedded rodata) since
    // packed/shielded libs often fold rodata into the code section.
    const ranges = [...mod.enumerateRanges("r--"), ...mod.enumerateRanges("r-x")];
    for (const r of ranges) {
        for (const needle of needles) {
            try {
                const matches = Memory.scanSync(r.base, r.size, asciiToHexPattern(needle));
                for (const m of matches) {
                    try {
                        const text = m.address.readUtf8String(
                            Math.min(needle.length + 32, 128)
                        ) ?? needle;
                        hits.push({
                            module: mod.name,
                            address: m.address.toString(),
                            offset: m.address.sub(mod.base).toString(),
                            text: text.split(/[\x00-\x1f]/)[0].slice(0, 120),
                        });
                    } catch (_) {
                        hits.push({
                            module: mod.name,
                            address: m.address.toString(),
                            offset: m.address.sub(mod.base).toString(),
                            text: needle,
                        });
                    }
                    if (hits.length > 200) return hits;
                }
            } catch (_) {
                /* range may be unreadable */
            }
        }
    }
    return hits;
}

/**
 * Scan a module for any string matching a hex-pattern (via scanSync's syntax).
 * Useful for LZ4 function prologue patterns once we know what they look like.
 */
function scanBytes(moduleName: string, pattern: string): Array<{ module: string; address: string; offset: string }> {
    const mod = Process.findModuleByName(moduleName);
    if (!mod) return [];
    const hits: Array<{ module: string; address: string; offset: string }> = [];
    const ranges = mod.enumerateRanges("r-x");
    for (const r of ranges) {
        try {
            const matches = Memory.scanSync(r.base, r.size, pattern);
            for (const m of matches) {
                hits.push({
                    module: mod.name,
                    address: m.address.toString(),
                    offset: m.address.sub(mod.base).toString(),
                });
                if (hits.length > 100) return hits;
            }
        } catch (_) {
            /* skip */
        }
    }
    return hits;
}

/**
 * Enumerate all symbols (exported AND local debug symbols) in a module.
 * Unity/IL2CPP strips exports but may retain some local symbols; this surfaces
 * them. Returns matches against the `pattern` regex.
 */
function findSymbols(moduleName: string, pattern: string): Array<{ module: string; name: string; address: string; type: string }> {
    const mod = Process.findModuleByName(moduleName);
    if (!mod) return [];
    const re = new RegExp(pattern, "i");
    const out: Array<{ module: string; name: string; address: string; type: string }> = [];
    try {
        const syms = (mod as any).enumerateSymbols?.() ?? [];
        for (const s of syms) {
            if (re.test(s.name)) {
                out.push({
                    module: mod.name,
                    name: s.name,
                    address: s.address.toString(),
                    type: s.type ?? "?",
                });
            }
        }
    } catch (_) {
        /* ignore */
    }
    return out;
}

/**
 * Hook dlopen so we can notify the host as soon as watched modules appear.
 * This is essential because libnative.so loads well after process spawn.
 */
function installDlopenWatcher(): void {
    const candidates = ["android_dlopen_ext", "dlopen", "__dlopen"];
    for (const sym of candidates) {
        let addr: NativePointer | null = null;
        // Try both APIs so the agent works across frida-gum 17 and 19.
        try {
            addr = (Module as any).getGlobalExportByName?.(sym) ?? null;
        } catch (_) {
            addr = null;
        }
        if (!addr) {
            try {
                addr = (Module as any).findExportByName?.(null, sym) ?? null;
            } catch (_) {
                addr = null;
            }
        }
        if (!addr) continue;
        Interceptor.attach(addr, {
            onEnter(args) {
                try {
                    const pathPtr = args[0];
                    if (!pathPtr.isNull()) {
                        this.path = pathPtr.readCString();
                    }
                } catch (_) {
                    /* best-effort */
                }
            },
            onLeave(retval) {
                if (!this.path) return;
                const path: string = this.path;
                const base = path.split("/").pop() || path;
                if (WATCH_MODULES.test(base) && !retval.isNull()) {
                    send({ type: "module_loaded", name: base, path, handle: retval.toString() });
                }
            },
        });
    }
}

// Startup hooks disabled — CrackProof shield detects Interceptor.attach on
// ptrace/dlopen within ~1s. Caller must now opt-in via RPC after renaming
// threads and (eventually) neutralizing the detector.

rpc.exports = {
    reportModules: (pattern?: string): ModuleInfo[] => {
        const re = pattern ? new RegExp(pattern, "i") : undefined;
        return snapshotModules(re);
    },
    findLz4Candidates: (moduleName?: string): Array<{ name: string; address: string }> => {
        return enumerateLz4Candidates(moduleName);
    },
    scanStrings: (moduleName: string, needles: string[]): StringHit[] => {
        return scanStrings(moduleName, needles);
    },
    scanBytes: (moduleName: string, pattern: string) => {
        return scanBytes(moduleName, pattern);
    },
    findSymbols: (moduleName: string, pattern: string) => {
        return findSymbols(moduleName, pattern);
    },
    installLz4Hook: (maxSnapshot?: number, prologueSkip?: number): boolean => {
        return installLz4Hook({ maxSnapshot, prologueSkip });
    },
    probeStalkerOnNativeLz4: (excludeLibnative?: boolean, broadFollow?: boolean): void => {
        probeStalkerOnNativeLz4(excludeLibnative ?? true, broadFollow ?? false);
    },
    probeStalkerHealth: (durationMs?: number): void => {
        probeStalkerHealth(durationMs ?? 3000);
    },
    probeStalkerHealthEvents: (durationMs?: number): void => {
        probeStalkerHealthEvents(durationMs ?? 3000);
    },
    installExitTraps: (): void => {
        installExitTraps();
    },
    installJavaExitTraps: (): void => {
        installJavaExitTraps();
    },
    discoverDeserializers: (): void => {
        discoverDeserializers();
    },
    installDeserializerHooks: (maxSnapshot?: number): void => {
        installDeserializerHooks({ maxSnapshot });
    },
    hookGenericDispatchStub: (): void => {
        hookGenericDispatchStub();
    },
    probeStalkerFollow: (): void => { probeStalkerFollow(); },
    probeStalkerOnGenericDispatch: (): void => { probeStalkerOnGenericDispatch(); },
    probeStalkerTransformOnDispatch: (): void => { probeStalkerTransformOnDispatch(); },
    probeStalkerOnLz4Decode: (): void => { probeStalkerOnLz4Decode(); },
    probeStalkerOnLz4Codec: (): void => { probeStalkerOnLz4Codec(); },
    probeStalkerTransformOnLz4Codec: (): void => { probeStalkerTransformOnLz4Codec(); },
    probeStalkerTransformOnMpReadBytes: (): void => { probeStalkerTransformOnMpReadBytes(); },
    findNonGenericMethodCandidates: (): void => { findNonGenericMethodCandidates(); },
    enumerateGallopHttpHelper: (): void => { enumerateGallopHttpHelper(); },
    probeStalkerTransformOnGallopHttp: (): void => { probeStalkerTransformOnGallopHttp(); },
    scanGallopCompressionMethods: (): void => { scanGallopCompressionMethods(); },
    probeStalkerTransformOnCryptAes: (): void => { probeStalkerTransformOnCryptAes(); },
    enumerateAllAssemblies: (): void => { enumerateAllAssemblies(); },
    scanAllAssembliesForCrypto: (): void => { scanAllAssembliesForCrypto(); },
    enumerateAssemblyClasses: (assemblyName: string): void => { enumerateAssemblyClasses(assemblyName); },
    enumerateClassWithAncestors: (fullName: string): void => { enumerateClassWithAncestors(fullName); },
    probeStalkerTransformOnTaskDeserialize: (): void => { probeStalkerTransformOnTaskDeserialize(); },
    interceptAttachOnTaskDeserialize: (maxAttach?: number): void => { interceptAttachOnTaskDeserialize(maxAttach); },
    enumerateLibNativeLz4: (): void => { enumerateLibNativeLz4(); },
    installLibNativeLz4Hooks: (maxSnapshot?: number): void => { installLibNativeLz4Hooks({ maxSnapshot }); },
    il2cppAttachSanity: (): void => { il2cppAttachSanity(); },
    captureCuteHttpDelegates: (maxSnap?: number): void => { captureCuteHttpDelegates(maxSnap); },
    installPtraceBypass: (): void => { installPtraceBypass(); },
    installDlopenWatcher: (): void => { installDlopenWatcher(); },
    enumerateSslModules: (): void => { enumerateSslModules(); },
    installSslReadProbe: () => installSslReadProbe(),
    installBoringSslProbes: () => installBoringSslProbes(),
    installConscryptEngineProbes: () => installConscryptEngineProbes(),
    scanAllSslSymbols: (): void => { scanAllSslSymbols(); },
    installAllSslHooks: () => installAllSslHooks(),
    installFixedSslHooks: () => installFixedSslHooks(),
    ping: (): string => "pong",
};

send({ type: "ready", agentVersion: "0.1.0", frida: Frida.version });
