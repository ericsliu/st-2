/**
 * Anti-debug bypass for Uma Musume.
 *
 * The game spawns a child process that calls ptrace(PTRACE_ATTACH, getppid())
 * on startup. Once attached, it is the sole tracer — Frida cannot attach to
 * the parent while this child is alive. If Frida DOES attach first (spawn
 * mode), the child's PTRACE_ATTACH call fails, and the game appears to stall
 * (libnative.so never loads).
 *
 * Bypass strategy: hook libc `ptrace` to always return 0 for PTRACE_ATTACH
 * (request == 16 on Linux arm64). Other requests fall through to the real
 * syscall.
 *
 * This module must be loaded BEFORE the anti-debug child does its ptrace
 * call. With Frida spawn mode and early injection, we run before main().
 */

const PTRACE_ATTACH = 16;
const PTRACE_TRACEME = 0;

export function installPtraceBypass(): boolean {
    // Try both "ptrace" (libc) and the syscall-level override.
    let addr: NativePointer | null = null;
    try {
        addr = (Module as any).getGlobalExportByName?.("ptrace") ?? null;
    } catch (_) {
        addr = null;
    }
    if (!addr) {
        try {
            addr = (Module as any).findExportByName?.(null, "ptrace") ?? null;
        } catch (_) {
            addr = null;
        }
    }
    if (!addr) {
        send({ type: "antidebug", status: "ptrace_not_found" });
        return false;
    }

    Interceptor.attach(addr, {
        onEnter(args) {
            this.request = args[0].toInt32();
            this.pid = args[1].toInt32();
        },
        onLeave(retval) {
            // For PTRACE_ATTACH and PTRACE_TRACEME, force success.
            // The anti-debug child calls PTRACE_ATTACH(parentPid).
            if (this.request === PTRACE_ATTACH || this.request === PTRACE_TRACEME) {
                if (retval.toInt32() < 0) {
                    send({
                        type: "antidebug",
                        event: "ptrace_attach_faked",
                        request: this.request,
                        target_pid: this.pid,
                    });
                }
                retval.replace(ptr("0"));
            }
        },
    });

    send({ type: "antidebug", status: "ptrace_hook_installed", addr: addr.toString() });
    return true;
}
