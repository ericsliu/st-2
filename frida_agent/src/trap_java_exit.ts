// @ts-nocheck
/**
 * Branch A1 probe from PACKET_INTERCEPTION_SPEC_ADDENDUM_3.md.
 *
 * Hook the Java-side quit methods so we can identify whether Uma's
 * "clean exit(0) at t+~2s after Hooking finished" is initiated from
 * managed Java code. If any of these fire during the death window,
 * the backtrace tells us which managed method called it, and the
 * fix is trivial (suppress the quit with `return` instead of the
 * original call).
 *
 * Hooks only log — they do NOT suppress the quit yet. We want the
 * diagnostic first.
 */

function javaBacktrace(): string[] {
    try {
        const Exception = Java.use("java.lang.Exception");
        const Log = Java.use("android.util.Log");
        const ex = Exception.$new();
        const bt = Log.getStackTraceString(ex);
        ex.$dispose?.();
        return bt.split("\n").map((s: string) => s.trim()).filter(Boolean).slice(0, 40);
    } catch (_) {
        return ["<java_bt_err>"];
    }
}

function safe(fn: () => void, label: string): void {
    try {
        fn();
    } catch (e: any) {
        send({ type: "java_trap_err", at: label, err: String(e?.message ?? e) });
    }
}

export function installJavaExitTraps(): void {
    if (typeof Java === "undefined" || !Java.available) {
        send({ type: "java_trap_err", at: "init", err: "Java runtime not available" });
        return;
    }
    Java.perform(() => {
        safe(() => {
            const Process = Java.use("android.os.Process");
            Process.killProcess.implementation = function (pid: number) {
                send({
                    type: "java_exit_trap",
                    fn: "android.os.Process.killProcess",
                    pid,
                    self_pid: Process.myPid(),
                    backtrace: javaBacktrace(),
                });
                return this.killProcess(pid);
            };
            send({ type: "java_exit_trap_installed", fn: "android.os.Process.killProcess" });
        }, "Process.killProcess");

        safe(() => {
            const Process = Java.use("android.os.Process");
            Process.sendSignal.implementation = function (pid: number, signal: number) {
                send({
                    type: "java_exit_trap",
                    fn: "android.os.Process.sendSignal",
                    pid,
                    signal,
                    self_pid: Process.myPid(),
                    backtrace: javaBacktrace(),
                });
                return this.sendSignal(pid, signal);
            };
            send({ type: "java_exit_trap_installed", fn: "android.os.Process.sendSignal" });
        }, "Process.sendSignal");

        safe(() => {
            const System = Java.use("java.lang.System");
            System.exit.implementation = function (code: number) {
                send({
                    type: "java_exit_trap",
                    fn: "java.lang.System.exit",
                    code,
                    backtrace: javaBacktrace(),
                });
                return this.exit(code);
            };
            send({ type: "java_exit_trap_installed", fn: "java.lang.System.exit" });
        }, "System.exit");

        safe(() => {
            const Runtime = Java.use("java.lang.Runtime");
            Runtime.exit.implementation = function (code: number) {
                send({
                    type: "java_exit_trap",
                    fn: "java.lang.Runtime.exit",
                    code,
                    backtrace: javaBacktrace(),
                });
                return this.exit(code);
            };
            Runtime.halt.implementation = function (code: number) {
                send({
                    type: "java_exit_trap",
                    fn: "java.lang.Runtime.halt",
                    code,
                    backtrace: javaBacktrace(),
                });
                return this.halt(code);
            };
            send({ type: "java_exit_trap_installed", fn: "java.lang.Runtime.exit+halt" });
        }, "Runtime.exit/halt");

        safe(() => {
            const Activity = Java.use("android.app.Activity");
            Activity.finish.implementation = function () {
                send({
                    type: "java_exit_trap",
                    fn: "android.app.Activity.finish",
                    cls: this.getClass().getName(),
                    backtrace: javaBacktrace(),
                });
                return this.finish();
            };
            Activity.finishAffinity.implementation = function () {
                send({
                    type: "java_exit_trap",
                    fn: "android.app.Activity.finishAffinity",
                    cls: this.getClass().getName(),
                    backtrace: javaBacktrace(),
                });
                return this.finishAffinity();
            };
            send({ type: "java_exit_trap_installed", fn: "android.app.Activity.finish+finishAffinity" });
        }, "Activity.finish");
    });
}
