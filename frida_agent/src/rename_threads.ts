// @ts-nocheck
/**
 * Rename Frida's own threads to non-Frida names.
 *
 * CrackProof detects Frida by scanning thread names (e.g. "gum-js-loop").
 * If we rename every frida-signature thread BEFORE the shield's detector
 * runs, we may slip past the check.
 *
 * Approach:
 *  - Enumerate threads via Process.enumerateThreads().
 *  - For each thread, read /proc/self/task/<tid>/comm.
 *  - If it matches a known Frida name, overwrite with a benign name
 *    (e.g. "Thread-ART-X") matching normal JVM thread naming.
 *
 * Known Frida thread names (from frida-gum source):
 *   gum-js-loop, gmain, gdbus, pool-frida, pool-spawn, pool-gum-js,
 *   frida-server, gum-thread
 */

const FRIDA_NAME_PATTERNS: RegExp[] = [
    /^gum-/,
    /^gmain$/,
    /^gdbus$/,
    /^pool-frida/,
    /^pool-spawn/,
    /^pool-gum-js/,
    /^frida-/,
];

function isFridaName(name: string): boolean {
    for (const re of FRIDA_NAME_PATTERNS) {
        if (re.test(name)) return true;
    }
    return false;
}

function readCommForTid(tid: number): string | null {
    try {
        const f = new File(`/proc/self/task/${tid}/comm`, "r");
        const s = f.readLine();
        f.close();
        return s.trim();
    } catch (_) {
        return null;
    }
}

function writeCommForTid(tid: number, name: string): boolean {
    try {
        const f = new File(`/proc/self/task/${tid}/comm`, "w");
        // comm is limited to TASK_COMM_LEN = 16 bytes including NUL.
        f.write(name.slice(0, 15));
        f.close();
        return true;
    } catch (_) {
        return false;
    }
}

export function renameFridaThreads(disguise: string = "Thread-ART"): {
    renamed: Array<{ tid: number; from: string; to: string }>;
    skipped: number;
    total: number;
} {
    const renamed: Array<{ tid: number; from: string; to: string }> = [];
    let skipped = 0;
    let total = 0;
    const threads = Process.enumerateThreads();
    total = threads.length;
    let idx = 0;
    for (const t of threads) {
        const name = readCommForTid(t.id);
        if (!name) {
            skipped++;
            continue;
        }
        if (!isFridaName(name)) {
            continue;
        }
        const newName = `${disguise}-${idx++}`;
        if (writeCommForTid(t.id, newName)) {
            renamed.push({ tid: t.id, from: name, to: newName });
        } else {
            skipped++;
        }
    }
    return { renamed, skipped, total };
}
