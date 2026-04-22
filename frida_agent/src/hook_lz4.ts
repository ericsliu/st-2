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

export function installLz4Hook(opts?: { maxSnapshot?: number }): boolean {
    if (installed) return true;
    const maxSnap = opts?.maxSnapshot ?? 64;

    const target = getExport("libnative.so", "LZ4_decompress_safe_ext");
    if (!target) {
        send({ type: "lz4_hook", status: "target_not_found" });
        return false;
    }

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
