/**
 * Trap calls to exit()/_exit()/abort() so we can see WHO is killing the game
 * when the LZ4 hook is installed. Logs a backtrace and the thread's comm name.
 *
 * The hooks DO NOT prevent the exit — they just log. We want the diagnostic,
 * not to stop the exit yet.
 */

function getExport(sym: string): NativePointer | null {
    try {
        const addr = (Module as any).findExportByName?.(null, sym) ?? null;
        if (addr) return addr;
    } catch (_) {
        /* fallthrough */
    }
    try {
        return (Module as any).getGlobalExportByName?.(sym) ?? null;
    } catch (_) {
        return null;
    }
}

function threadName(): string {
    try {
        const fd = new File("/proc/self/comm", "r");
        const s = fd.readLine();
        fd.close();
        return s.trim();
    } catch (_) {
        return "?";
    }
}

function btStrings(ctx: CpuContext): string[] {
    try {
        return Thread.backtrace(ctx, Backtracer.ACCURATE).map((a) => {
            const s: any = DebugSymbol.fromAddress(a);
            const off = s.offset ?? 0;
            return `${a} ${s.moduleName ?? "?"}!${s.name ?? "?"}+0x${off.toString(16)}`;
        });
    } catch (_) {
        return ["<backtrace_err>"];
    }
}

export function installExitTraps(): void {
    // libc exit-family
    for (const sym of ["_exit", "exit", "abort", "_Exit", "pthread_exit"]) {
        const addr = getExport(sym);
        if (!addr) continue;
        Interceptor.attach(addr, {
            onEnter(args) {
                send({
                    type: "exit_trap",
                    fn: sym,
                    tid: Process.getCurrentThreadId(),
                    arg0: args[0].toInt32(),
                    backtrace: btStrings(this.context),
                });
            },
        });
        send({ type: "exit_trap_installed", fn: sym, addr: addr.toString() });
    }
    // raw syscall — arg0 = syscall number (94 = exit_group on arm64)
    const sc = getExport("syscall");
    if (sc) {
        Interceptor.attach(sc, {
            onEnter(args) {
                const no = args[0].toInt32();
                // arm64: SYS_exit=93, SYS_exit_group=94, SYS_kill=129, SYS_tgkill=131, SYS_tkill=130
                if (no === 93 || no === 94 || no === 129 || no === 130 || no === 131) {
                    send({
                        type: "exit_trap",
                        fn: `syscall(${no})`,
                        tid: Process.getCurrentThreadId(),
                        arg0: args[1].toInt32(),
                        backtrace: btStrings(this.context),
                    });
                }
            },
        });
        send({ type: "exit_trap_installed", fn: "syscall", addr: sc.toString() });
    }
    // kill/tgkill/raise wrappers
    for (const sym of ["kill", "tgkill", "tkill", "raise"]) {
        const addr = getExport(sym);
        if (!addr) continue;
        Interceptor.attach(addr, {
            onEnter(args) {
                send({
                    type: "exit_trap",
                    fn: sym,
                    tid: Process.getCurrentThreadId(),
                    arg0: args[0].toInt32(),
                    arg1: args[1]?.toInt32?.() ?? -1,
                    arg2: args[2]?.toInt32?.() ?? -1,
                    backtrace: btStrings(this.context),
                });
            },
        });
        send({ type: "exit_trap_installed", fn: sym, addr: addr.toString() });
    }
}
