#!/usr/bin/env python3
"""Branch C1 probe (PACKET_INTERCEPTION_SPEC_ADDENDUM_3).

Load the full frida_agent/dist/agent.js into the running Uma process via
the ZygiskFrida gadget, then invoke RPC:
  1. discoverDeserializers() — resolve MessagePack assembly + Deserialize
     overloads; confirms the shield tolerates frida-il2cpp-bridge walking.
  2. (--hook) installDeserializerHooks() — wrap each overload to log calls.

Startup mirrors frida_a1_probe.py: force-stop Uma, monkey-launch, poll for
main pid via exact /proc/<pid>/cmdline match, forward tcp:27042, wait for
gadget listener, attach to the gadget proc matching the main pid.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import frida

DEVICE = "127.0.0.1:5555"
PACKAGE = "com.cygames.umamusume"
GADGET_HOST = "127.0.0.1:27042"
AGENT_JS = Path(__file__).resolve().parents[1] / "frida_agent" / "dist" / "agent.js"


def adb(*args: str, check: bool = True) -> str:
    r = subprocess.run(["adb", "-s", DEVICE, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {args}: rc={r.returncode} stderr={r.stderr}")
    return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hook", action="store_true", help="After discovery, install hooks")
    ap.add_argument("--stub-hook", action="store_true", help="After discovery, install ONE Interceptor.attach on the shared generic dispatch VA")
    ap.add_argument("--stalker-follow", action="store_true", help="Stalker.follow every thread (survival test, no hooks)")
    ap.add_argument("--stalker-probe", action="store_true", help="Stalker.follow + addCallProbe on the generic dispatch VA")
    ap.add_argument("--stalker-transform", action="store_true", help="Stalker.follow with transform (catches indirect BLR) on dispatch VA")
    ap.add_argument("--stalker-lz4", action="store_true", help="Stalker.follow + addCallProbe on LZ4MessagePackSerializer.Decode(Byte[])")
    ap.add_argument("--stalker-lz4codec", action="store_true", help="Stalker.follow + addCallProbe on MessagePack.LZ4.LZ4Codec.Decode (6-arg raw LZ4)")
    ap.add_argument("--stalker-transform-lz4codec", action="store_true", help="Stalker.follow + transform on LZ4Codec.Decode (catches indirect BLR)")
    ap.add_argument("--stalker-transform-readbytes", action="store_true", help="Stalker.follow + transform on MessagePackBinary.ReadBytes (catches indirect BLR)")
    ap.add_argument("--catalog", action="store_true", help="Enumerate non-generic MessagePack methods (VA catalog)")
    ap.add_argument("--ssl-enum", action="store_true", help="Enumerate SSL/TLS/crypto modules and SSL-ish symbols (NO hooks)")
    ap.add_argument("--ssl-probe", action="store_true", help="Enumerate SSL modules then install ONE Interceptor.attach on SSL_read")
    ap.add_argument("--boringssl-probe", action="store_true", help="Install SSL_read+SSL_write hooks in libssl.so (BoringSSL) specifically")
    ap.add_argument("--conscrypt-engine", action="store_true", help="Install Conscrypt ENGINE_SSL_{read,write}_direct hooks in libjavacrypto.so")
    ap.add_argument("--wide-ssl", action="store_true", help="Scan ALL loaded modules for SSL symbols, then hook every match (except libil2cpp)")
    ap.add_argument("--fixed-ssl", action="store_true", help="Hook a fixed list of SSL_* symbols via Module.findGlobalExportByName (safest wide-coverage path)")
    ap.add_argument("--gallop-enum", action="store_true", help="Runtime-enumerate Gallop.HttpHelper + related classes; emit method list with VAs (no hooks)")
    ap.add_argument("--gallop-transform", action="store_true", help="Stalker.follow + transform on Gallop.HttpHelper.CompressRequest and DecompressResponse (plaintext MessagePack boundary)")
    ap.add_argument("--gallop-scan", action="store_true", help="Broad scan of umamusume assembly for any compress/decompress/encrypt/decrypt/coneshell methods (no hooks)")
    ap.add_argument("--cryptaes-transform", action="store_true", help="Stalker.follow + transform on Gallop.CryptAES EncryptRJ256/DecryptRJ256/Decrypt byte[] overloads (AES-256 HTTP body boundary)")
    ap.add_argument("--enum-assemblies", action="store_true", help="List every loaded IL2CPP assembly + its class count (no hooks, metadata only)")
    ap.add_argument("--scan-all-crypto", action="store_true", help="Broad scan of EVERY non-framework assembly for compress/encrypt/request/response methods")
    ap.add_argument("--enum-asm-classes", type=str, default=None, help="Enumerate all classes in a specific IL2CPP assembly (pass the assembly name)")
    ap.add_argument("--enum-class", type=str, default=None, help="Enumerate a class + its entire ancestor chain, emitting every method on every level (full name e.g. Gallop.BannerUrlTask)")
    ap.add_argument("--task-deserialize-transform", action="store_true", help="Stalker.transform across every Gallop.*Task.Deserialize(byte[]) method in umamusume.Http — plaintext MessagePack response boundary for every API endpoint at once")
    ap.add_argument("--task-deserialize-intercept", type=int, nargs='?', const=50, default=None, help="Sanity probe: Interceptor.attach directly on N (default 50) resolved Gallop.*Task.Deserialize VAs to check whether Stalker has a gating bug")
    ap.add_argument("--lz4-native", action="store_true", help="Hook libnative.so exported LZ4_decompress_safe_ext — Uma's wire format is TLS(LZ4(msgpack)) so this is the plaintext msgpack boundary (CarrotJuicer-style)")
    ap.add_argument("--lz4-skip", type=int, default=0, help="Bytes past LZ4 function entry to hook (evade shield prologue-byte integrity check). 0x20 = past all prologue register saves before first arg use.")
    ap.add_argument("--lz4-stalker", action="store_true", help="Stalker-based LZ4 trace — instruments BL-to-LZ4 call sites in IL2CPP threads instead of patching libnative.so. Evades shield prologue-byte check.")
    ap.add_argument("--lz4-stalker-no-exclude", action="store_true", help="With --lz4-stalker: do NOT Stalker.exclude libnative.so (debug / explicit follow into LZ4 body). Slow.")
    ap.add_argument("--lz4-stalker-broad", action="store_true", help="With --lz4-stalker: follow EVERY thread except explicit frida/render/gc excludes. Useful when whitelist misses the HTTP worker.")
    ap.add_argument("--il2cpp-sanity", action="store_true", help="Definitive diagnostic: hook System.Object.ToString (Interceptor.attach AND method.implementation swap), then self-invoke from agent. Tells us if libil2cpp hooks work at all.")
    ap.add_argument("--libnative-lz4-enum", action="store_true", help="Enumerate LibNative.LZ4.* classes/methods with VAs (no hooks). Cygames' C# wrapper around their native LZ4 plugin — prime interception target in libil2cpp.")
    ap.add_argument("--libnative-lz4-hook", action="store_true", help="Interceptor.attach on every LibNative.LZ4.* Decompress* method with byte[] in/out. Captures LZ4 plaintext at managed layer, avoiding libnative.so prologue hash.")
    ap.add_argument("--libnative-lz4-snap", type=int, default=64, help="Bytes to snapshot per hit (in+out), clamped 16..256")
    ap.add_argument("--stalker-health", action="store_true", help="Minimal Stalker health check: self-stalk the RPC thread and count block compiles. If 0, Stalker is broken in this gadget env.")
    ap.add_argument("--stalker-health-events", action="store_true", help="Stalker health check via the `events` API (onReceive callbacks) instead of `transform`")
    ap.add_argument("--stalker-health-ms", type=int, default=3000, help="Duration of --stalker-health self-stalk in ms")
    ap.add_argument("--libnative-strings", action="store_true", help="Scan libnative.so read-only memory for LZ4/mbedtls/curl strings (pure-read, no hooks)")
    ap.add_argument("--libnative-symbols", action="store_true", help="Enumerate libnative.so symbols matching LZ4/mbedtls/curl/ssl patterns (no hooks)")
    ap.add_argument("--capture-cute-http", action="store_true", help="Hook Cute.Http.HttpManager set_DecompressFunc/set_CompressFunc + read current Instance delegates; when captured, hook the Func<byte[],byte[]>.Invoke method_ptr with snapshots.")
    ap.add_argument("--capture-cute-http-snap", type=int, default=256, help="Bytes to snapshot per hit on captured Func<byte[],byte[]> invocations")
    ap.add_argument("--duration", type=float, default=30.0, help="Watch seconds after attach")
    ap.add_argument("--startup-wait", type=float, default=8.0, help="Seconds to wait for main pid")
    ap.add_argument("--no-launch", action="store_true", help="Skip launch; attach to already-running Uma")
    ap.add_argument("--discover-delay", type=float, default=2.0,
                    help="Seconds to wait after attach before calling discoverDeserializers (let libil2cpp finish loading)")
    args = ap.parse_args()

    if not AGENT_JS.exists():
        print(f"[!] agent not built: {AGENT_JS}", file=sys.stderr)
        return 1

    if not args.no_launch:
        print("[*] force-stop Uma")
        adb("shell", f"am force-stop {PACKAGE}", check=False)
        time.sleep(0.5)
        print("[*] monkey launch")
        adb("shell", f"monkey -p {PACKAGE} -c android.intent.category.LAUNCHER 1", check=False)

    deadline = time.monotonic() + args.startup_wait
    main_pid = None
    while time.monotonic() < deadline:
        out = adb("shell", f"pidof {PACKAGE}", check=False).strip()
        pids = [int(p) for p in out.split() if p.isdigit()]
        for pid in pids:
            cmd = adb("shell", f"cat /proc/{pid}/cmdline 2>/dev/null", check=False).replace("\x00", " ").strip()
            first = cmd.split()[0] if cmd else ""
            if first == PACKAGE:
                main_pid = pid
                break
        if main_pid is not None:
            break
        time.sleep(0.25)

    if main_pid is None:
        print("[!] no main Uma pid found", file=sys.stderr)
        return 2
    print(f"[*] main Uma pid = {main_pid}")

    adb("forward", "tcp:27042", "tcp:27042", check=False)

    mgr = frida.get_device_manager()
    dev = None
    t0 = time.time()
    while time.time() - t0 < 12:
        try:
            dev = mgr.add_remote_device(GADGET_HOST)
            procs = dev.enumerate_processes()
            if procs:
                break
        except Exception:
            pass
        try:
            mgr.remove_remote_device(GADGET_HOST)
        except Exception:
            pass
        time.sleep(0.2)
    if not dev:
        print("[!] gadget never appeared", file=sys.stderr)
        return 3

    procs = dev.enumerate_processes()
    match = [p for p in procs if p.pid == main_pid]
    if not match:
        # Cold recovery: if we just launched, give ZygiskFrida extra time to catch
        # the main fork (sometimes helper sub-processes register first).
        if not args.no_launch:
            print(f"[*] no gadget proc for main pid={main_pid}; gadget has {[p.pid for p in procs]}; waiting up to 8s for main fork...", flush=True)
            recov_deadline = time.monotonic() + 8.0
            while time.monotonic() < recov_deadline:
                time.sleep(0.4)
                try:
                    procs = dev.enumerate_processes()
                except Exception:
                    continue
                match = [p for p in procs if p.pid == main_pid]
                if match:
                    print(f"[*] cold recovery succeeded; gadget now in main pid={main_pid}", flush=True)
                    break
        if not match:
            # With --no-launch we may have picked a stale/ephemeral pid from
            # pidof. If exactly one process is gadget-registered, fall back
            # to it rather than bailing.
            if args.no_launch and len(procs) == 1:
                print(f"[*] main_pid {main_pid} stale; falling back to sole gadget-registered pid {procs[0].pid} ({procs[0].name!r})", flush=True)
                match = [procs[0]]
            else:
                print(f"[!] gadget not in main pid; main={main_pid}, gadget in={[(p.pid, p.name) for p in procs]}", file=sys.stderr)
                return 6

    session = dev.attach(match[0].pid)
    attach_t = time.time()

    died = {"v": False}
    def on_detached(*a):
        print(f"[{time.time()-attach_t:5.1f}s] SESSION DETACHED {a}", flush=True)
        died["v"] = True
    session.on("detached", on_detached)

    msgs = {"n": 0}
    def on_message(msg, data):
        msgs["n"] += 1
        t = time.time() - attach_t
        if msg["type"] == "send":
            p = msg.get("payload") or {}
            print(f"[{t:5.1f}s] {p}", flush=True)
        elif msg["type"] == "error":
            print(f"[{t:5.1f}s] [ERR] {msg.get('stack', msg)}", flush=True)
        else:
            print(f"[{t:5.1f}s] [?] {msg}", flush=True)

    script = session.create_script(AGENT_JS.read_text())
    script.on("message", on_message)
    script.load()
    print(f"[{time.time()-attach_t:.1f}s] agent loaded, waiting {args.discover_delay}s for libil2cpp...", flush=True)

    # No startup hooks — shield kills fast if we install ptrace/dlopen hooks.
    # Rename Frida threads first, then discover IL2CPP, then optionally hook.
    rename_js = r"""
var opendir = new NativeFunction(Module.getGlobalExportByName("opendir"), "pointer", ["pointer"]);
var readdir = new NativeFunction(Module.getGlobalExportByName("readdir"), "pointer", ["pointer"]);
var closedir = new NativeFunction(Module.getGlobalExportByName("closedir"), "int", ["pointer"]);
var PATTERNS = [/^gum-/, /^gmain$/, /^gdbus$/, /^pool-frida/, /^pool-spawn/, /^pool-gum-js/, /^frida-/];
function isFrida(n) { for (var i=0; i<PATTERNS.length; i++) if (PATTERNS[i].test(n)) return true; return false; }
function readComm(tid) {
  try { var f = new File("/proc/self/task/" + tid + "/comm", "r"); var s = f.readLine(); f.close(); return s.replace(/[\r\n\x00]/g,"").trim(); }
  catch (e) { return null; }
}
function writeComm(tid, name) {
  try { var f = new File("/proc/self/task/" + tid + "/comm", "w"); f.write(name.slice(0,15)); f.close(); return true; }
  catch (e) { return false; }
}
function sweep(tag) {
  var tids = [];
  var dir = opendir(Memory.allocUtf8String("/proc/self/task"));
  while (true) {
    var ent = readdir(dir);
    if (ent.isNull()) break;
    var name = ent.add(19).readCString();
    if (!name || name === "." || name === "..") continue;
    var tid = parseInt(name, 10); if (!isNaN(tid)) tids.push(tid);
  }
  closedir(dir);
  var renamed = [];
  for (var i=0; i<tids.length; i++) {
    var n = readComm(tids[i]); if (!n || !isFrida(n)) continue;
    var nn = "Thread-JVM-"+i;
    if (writeComm(tids[i], nn)) renamed.push({tid: tids[i], from: n, to: nn});
  }
  send({type:"thread_sweep", tag: tag, total: tids.length, renamed: renamed});
}
sweep("c1_initial");
setTimeout(function(){sweep("c1_200ms")}, 200);
setTimeout(function(){sweep("c1_1s")}, 1000);
setTimeout(function(){sweep("c1_3s")}, 3000);
"""
    rename_script = session.create_script(rename_js)
    rename_script.on("message", on_message)
    rename_script.load()
    print(f"[{time.time()-attach_t:.1f}s] thread rename script loaded", flush=True)

    # Sentinel: confirm we attached to the REAL Uma main process, not a helper.
    sentinel_result = {"got": None}
    def on_sentinel(msg, data):
        if msg.get("type") == "send":
            p = msg.get("payload") or {}
            if isinstance(p, dict) and p.get("type") == "sentinel":
                sentinel_result["got"] = p
        elif msg.get("type") == "error":
            print(f"[{time.time()-attach_t:5.1f}s] [sentinel ERR] {msg.get('stack', msg)}", flush=True)

    sentinel_js = r"""
var il2cpp = Process.findModuleByName("libil2cpp.so");
var javacrypto = Process.findModuleByName("libjavacrypto.so");
var cmdline = null;
try {
    var f = new File("/proc/self/cmdline", "r");
    cmdline = f.readLine();
    f.close();
    if (cmdline) cmdline = cmdline.split("\u0000")[0];
} catch(e) {}
send({
    type: "sentinel",
    cmdline: cmdline,
    il2cpp: !!il2cpp,
    libjavacrypto: !!javacrypto,
    il2cppBase: il2cpp ? il2cpp.base.toString() : null
});
"""
    sentinel_script = session.create_script(sentinel_js)
    sentinel_script.on("message", on_sentinel)
    sentinel_script.load()
    s_deadline = time.time() + 3.0
    while time.time() < s_deadline and sentinel_result["got"] is None:
        time.sleep(0.1)
    sp = sentinel_result["got"]
    if sp is None:
        print(f"[!] sentinel never fired; attach may be to wrong process", file=sys.stderr)
        try:
            session.detach()
        except Exception:
            pass
        return 7
    print(f"[{time.time()-attach_t:.1f}s] sentinel: cmdline={sp.get('cmdline')!r} il2cpp={sp.get('il2cpp')} libjavacrypto={sp.get('libjavacrypto')} il2cppBase={sp.get('il2cppBase')}", flush=True)
    if sp.get("cmdline") != PACKAGE or not sp.get("il2cpp"):
        print(f"[!] wrong-process attach: cmdline={sp.get('cmdline')!r} il2cpp={sp.get('il2cpp')}; detaching", file=sys.stderr)
        try:
            session.detach()
        except Exception:
            pass
        return 8

    time.sleep(args.discover_delay)
    if died["v"]:
        print("[!] session died before discover call", file=sys.stderr)
        return 5

    ssl_only = args.ssl_enum or args.ssl_probe or args.boringssl_probe or args.conscrypt_engine or args.wide_ssl or args.fixed_ssl or args.gallop_enum or args.gallop_transform or args.gallop_scan or args.cryptaes_transform or args.enum_assemblies or args.scan_all_crypto or bool(args.enum_asm_classes) or bool(args.enum_class) or args.task_deserialize_transform or (args.task_deserialize_intercept is not None) or args.lz4_native or args.lz4_stalker or args.stalker_health or args.stalker_health_events or args.libnative_lz4_enum or args.libnative_lz4_hook or args.il2cpp_sanity or args.libnative_strings or args.libnative_symbols or args.capture_cute_http
    if not ssl_only:
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> discoverDeserializers()", flush=True)
            script.exports_sync.discover_deserializers()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] discover RPC err: {e}", flush=True)

    if (args.ssl_enum or args.ssl_probe) and not died["v"]:
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> enumerateSslModules()", flush=True)
            script.exports_sync.enumerate_ssl_modules()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] ssl-enum RPC err: {e}", flush=True)

    if args.ssl_probe and not died["v"]:
        time.sleep(2.0)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> installSslReadProbe()", flush=True)
            result = script.exports_sync.install_ssl_read_probe()
            print(f"[{time.time()-attach_t:.1f}s] ssl-probe result: {result}", flush=True)
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] ssl-probe RPC err: {e}", flush=True)

    if args.boringssl_probe and not died["v"]:
        time.sleep(args.discover_delay)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> installBoringSslProbes()", flush=True)
            result = script.exports_sync.install_boring_ssl_probes()
            print(f"[{time.time()-attach_t:.1f}s] boringssl-probe result: {result}", flush=True)
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] boringssl-probe RPC err: {e}", flush=True)

    if args.conscrypt_engine and not died["v"]:
        time.sleep(args.discover_delay)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> installConscryptEngineProbes()", flush=True)
            result = script.exports_sync.install_conscrypt_engine_probes()
            print(f"[{time.time()-attach_t:.1f}s] conscrypt-engine result: {result}", flush=True)
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] conscrypt-engine RPC err: {e}", flush=True)

    if args.wide_ssl and not died["v"]:
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> scanAllSslSymbols()", flush=True)
            script.exports_sync.scan_all_ssl_symbols()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] scan-all-ssl RPC err: {e}", flush=True)
        time.sleep(2.0)
        if not died["v"]:
            try:
                print(f"[{time.time()-attach_t:.1f}s] -> installAllSslHooks()", flush=True)
                result = script.exports_sync.install_all_ssl_hooks()
                if isinstance(result, dict):
                    total = result.get("total")
                    hooks = result.get("hooks") or []
                    print(f"[{time.time()-attach_t:.1f}s] install-all-ssl total={total} first5={hooks[:5]}", flush=True)
                else:
                    print(f"[{time.time()-attach_t:.1f}s] install-all-ssl result: {result}", flush=True)
            except Exception as e:
                print(f"[{time.time()-attach_t:.1f}s] install-all-ssl RPC err: {e}", flush=True)

    if args.fixed_ssl and not died["v"]:
        time.sleep(args.discover_delay)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> installFixedSslHooks()", flush=True)
            result = script.exports_sync.install_fixed_ssl_hooks()
            if isinstance(result, dict):
                total = result.get("total")
                hooks = result.get("hooks") or []
                print(f"[{time.time()-attach_t:.1f}s] fixed-ssl total={total} hooks={hooks}", flush=True)
            else:
                print(f"[{time.time()-attach_t:.1f}s] fixed-ssl result: {result}", flush=True)
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] fixed-ssl RPC err: {e}", flush=True)

    if args.gallop_enum and not died["v"]:
        time.sleep(args.discover_delay)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> enumerateGallopHttpHelper()", flush=True)
            script.exports_sync.enumerate_gallop_http_helper()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] gallop-enum RPC err: {e}", flush=True)

    if args.gallop_transform and not died["v"]:
        time.sleep(args.discover_delay)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> probeStalkerTransformOnGallopHttp()", flush=True)
            script.exports_sync.probe_stalker_transform_on_gallop_http()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] gallop-transform RPC err: {e}", flush=True)

    if args.gallop_scan and not died["v"]:
        time.sleep(args.discover_delay)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> scanGallopCompressionMethods()", flush=True)
            script.exports_sync.scan_gallop_compression_methods()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] gallop-scan RPC err: {e}", flush=True)

    if args.cryptaes_transform and not died["v"]:
        time.sleep(args.discover_delay)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> probeStalkerTransformOnCryptAES()", flush=True)
            script.exports_sync.probe_stalker_transform_on_crypt_aes()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] cryptaes-transform RPC err: {e}", flush=True)

    if args.enum_assemblies and not died["v"]:
        time.sleep(args.discover_delay)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> enumerateAllAssemblies()", flush=True)
            script.exports_sync.enumerate_all_assemblies()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] enum-assemblies RPC err: {e}", flush=True)

    if args.scan_all_crypto and not died["v"]:
        time.sleep(args.discover_delay)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> scanAllAssembliesForCrypto()", flush=True)
            script.exports_sync.scan_all_assemblies_for_crypto()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] scan-all-crypto RPC err: {e}", flush=True)

    if args.enum_asm_classes and not died["v"]:
        time.sleep(args.discover_delay)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> enumerateAssemblyClasses({args.enum_asm_classes!r})", flush=True)
            script.exports_sync.enumerate_assembly_classes(args.enum_asm_classes)
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] enum-asm-classes RPC err: {e}", flush=True)

    if args.enum_class and not died["v"]:
        time.sleep(args.discover_delay)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> enumerateClassWithAncestors({args.enum_class!r})", flush=True)
            script.exports_sync.enumerate_class_with_ancestors(args.enum_class)
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] enum-class RPC err: {e}", flush=True)

    if args.task_deserialize_transform and not died["v"]:
        time.sleep(args.discover_delay)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> probeStalkerTransformOnTaskDeserialize()", flush=True)
            script.exports_sync.probe_stalker_transform_on_task_deserialize()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] task-deserialize-transform RPC err: {e}", flush=True)

    if args.task_deserialize_intercept is not None and not died["v"]:
        time.sleep(args.discover_delay)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> interceptAttachOnTaskDeserialize({args.task_deserialize_intercept})", flush=True)
            script.exports_sync.intercept_attach_on_task_deserialize(args.task_deserialize_intercept)
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] task-deserialize-intercept RPC err: {e}", flush=True)

    if args.lz4_native and not died["v"]:
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> installLz4Hook(prologueSkip=0x{args.lz4_skip:x})", flush=True)
            result = script.exports_sync.install_lz4_hook(256, args.lz4_skip)
            print(f"[{time.time()-attach_t:.1f}s] lz4-native installed: {result}", flush=True)
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] lz4-native RPC err: {e}", flush=True)

    if args.lz4_stalker and not died["v"]:
        try:
            exclude = not args.lz4_stalker_no_exclude
            broad = args.lz4_stalker_broad
            print(f"[{time.time()-attach_t:.1f}s] -> probeStalkerOnNativeLz4(exclude={exclude}, broad={broad})", flush=True)
            script.exports_sync.probe_stalker_on_native_lz4(exclude, broad)
            print(f"[{time.time()-attach_t:.1f}s] lz4-stalker boot triggered", flush=True)
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] lz4-stalker RPC err: {e}", flush=True)

    if args.il2cpp_sanity and not died["v"]:
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> il2cppAttachSanity()", flush=True)
            script.exports_sync.il2cpp_attach_sanity()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] il2cpp-sanity RPC err: {e}", flush=True)

    if args.libnative_lz4_enum and not died["v"]:
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> enumerateLibNativeLz4()", flush=True)
            script.exports_sync.enumerate_lib_native_lz4()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] libnative-lz4-enum RPC err: {e}", flush=True)

    if args.libnative_lz4_hook and not died["v"]:
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> installLibNativeLz4Hooks(snap={args.libnative_lz4_snap})", flush=True)
            script.exports_sync.install_lib_native_lz4_hooks(args.libnative_lz4_snap)
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] libnative-lz4-hook RPC err: {e}", flush=True)

    if args.libnative_strings and not died["v"]:
        try:
            needles = [
                "LZ4_decompress_safe_ext",
                "LZ4_compress_default_ext",
                "LZ4_decompress_safe",
                "LZ4_compress",
                "LZ4_decompress",
                "mbedtls_ssl_read",
                "mbedtls_ssl_write",
                "mbedtls_ssl_init",
                "mbedtls_ssl_handshake",
                "ssl_decrypt_buf",
                "ssl_get_record",
                "curl_easy_perform",
                "curl_easy_setopt",
                "CURLE_",
                "libcurl",
                "CURLOPT_",
                "Error decompressing",
                "Error compressing",
            ]
            print(f"[{time.time()-attach_t:.1f}s] -> scanStrings(libnative.so, {len(needles)} needles)", flush=True)
            hits = script.exports_sync.scan_strings("libnative.so", needles)
            # Group hits by which needle matched
            by_needle: dict[str, list] = {}
            for h in hits:
                key = h.get("text", "")[:60]
                by_needle.setdefault(key, []).append(h)
            print(f"[{time.time()-attach_t:.1f}s] libnative_strings: {len(hits)} hits across {len(by_needle)} unique strings", flush=True)
            for text, rows in sorted(by_needle.items()):
                first = rows[0]
                print(f"    {first.get('offset')} ({len(rows)}x) {text!r}", flush=True)
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] libnative-strings RPC err: {e}", flush=True)

    if args.libnative_symbols and not died["v"]:
        try:
            pattern = r"LZ4|mbedtls|ssl_|curl_|CURL|decrypt|decompress|_read|_write"
            print(f"[{time.time()-attach_t:.1f}s] -> findSymbols(libnative.so, /{pattern}/)", flush=True)
            syms = script.exports_sync.find_symbols("libnative.so", pattern)
            print(f"[{time.time()-attach_t:.1f}s] libnative_symbols: {len(syms)} matches", flush=True)
            for s in syms[:60]:
                print(f"    [{s.get('type')}] {s.get('address')} {s.get('name')}", flush=True)
            if len(syms) > 60:
                print(f"    ... +{len(syms)-60} more", flush=True)
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] libnative-symbols RPC err: {e}", flush=True)

    if args.capture_cute_http and not died["v"]:
        try:
            snap = max(16, min(4096, int(args.capture_cute_http_snap)))
            print(f"[{time.time()-attach_t:.1f}s] -> captureCuteHttpDelegates(maxSnap={snap})", flush=True)
            script.exports_sync.capture_cute_http_delegates(snap)
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] capture-cute-http RPC err: {e}", flush=True)

    if args.stalker_health and not died["v"]:
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> probeStalkerHealth(ms={args.stalker_health_ms})", flush=True)
            script.exports_sync.probe_stalker_health(args.stalker_health_ms)
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] stalker-health RPC err: {e}", flush=True)

    if args.stalker_health_events and not died["v"]:
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> probeStalkerHealthEvents(ms={args.stalker_health_ms})", flush=True)
            script.exports_sync.probe_stalker_health_events(args.stalker_health_ms)
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] stalker-health-events RPC err: {e}", flush=True)

    if args.catalog and not died["v"]:
        time.sleep(0.5)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> findNonGenericMethodCandidates()", flush=True)
            script.exports_sync.find_non_generic_method_candidates()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] catalog RPC err: {e}", flush=True)

    if args.stalker_transform and not died["v"]:
        time.sleep(1.0)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> probeStalkerTransformOnDispatch()", flush=True)
            script.exports_sync.probe_stalker_transform_on_dispatch()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] stalker-transform RPC err: {e}", flush=True)

    if args.stalker_lz4 and not died["v"]:
        time.sleep(1.0)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> probeStalkerOnLz4Decode()", flush=True)
            script.exports_sync.probe_stalker_on_lz4_decode()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] stalker-lz4 RPC err: {e}", flush=True)

    if args.stalker_lz4codec and not died["v"]:
        time.sleep(1.0)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> probeStalkerOnLz4Codec()", flush=True)
            script.exports_sync.probe_stalker_on_lz4_codec()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] stalker-lz4codec RPC err: {e}", flush=True)

    if args.stalker_transform_lz4codec and not died["v"]:
        time.sleep(1.0)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> probeStalkerTransformOnLz4Codec()", flush=True)
            script.exports_sync.probe_stalker_transform_on_lz4_codec()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] stalker-transform-lz4codec RPC err: {e}", flush=True)

    if args.stalker_transform_readbytes and not died["v"]:
        time.sleep(1.0)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> probeStalkerTransformOnMpReadBytes()", flush=True)
            script.exports_sync.probe_stalker_transform_on_mp_read_bytes()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] stalker-transform-readbytes RPC err: {e}", flush=True)

    if (args.stalker_follow or args.stalker_probe) and not died["v"]:
        time.sleep(1.0)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> probeStalkerFollow()", flush=True)
            script.exports_sync.probe_stalker_follow()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] stalker-follow RPC err: {e}", flush=True)

    if args.stalker_probe and not died["v"]:
        time.sleep(2.0)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> probeStalkerOnGenericDispatch()", flush=True)
            script.exports_sync.probe_stalker_on_generic_dispatch()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] stalker-probe RPC err: {e}", flush=True)

    if args.stub_hook and not died["v"]:
        time.sleep(1.0)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> hookGenericDispatchStub()", flush=True)
            script.exports_sync.hook_generic_dispatch_stub()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] stub-hook RPC err: {e}", flush=True)

    if args.hook and not died["v"]:
        time.sleep(1.0)
        try:
            print(f"[{time.time()-attach_t:.1f}s] -> installDeserializerHooks()", flush=True)
            script.exports_sync.install_deserializer_hooks()
        except Exception as e:
            print(f"[{time.time()-attach_t:.1f}s] hook RPC err: {e}", flush=True)

    end = time.time() + args.duration
    while time.time() < end and not died["v"]:
        time.sleep(0.2)
    time.sleep(0.5)
    print(f"\n[summary] messages={msgs['n']} died_early={died['v']}")
    try:
        session.detach()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
