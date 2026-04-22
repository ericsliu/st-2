# Packet Capture Runtime — Design

How the Uma packet-capture stack actually runs, end-to-end: gadget
injection, hardening, hook lifecycle, and the capture trigger. This is
the "how to operate it" companion to `docs/packet_state_layer.md` (the
"what the data means" doc) and `docs/PACKET_INTERCEPTION_WS3_REPORT.md`
(the "how the schema is shaped" doc).

## Runtime stack

```
    ┌────────── host (macOS) ──────────┐        ┌───── device (BlueStacks) ─────┐
    │                                  │        │                                │
    │   scripts/frida_c1_probe.py      │──adb──▶│   com.cygames.umamusume (pid)  │
    │     ├─ attach via tcp:27042      │        │    └─ libgadget.so (ZygiskFrida)│
    │     ├─ load frida_agent/dist     │◀──IPC──│        └─ frida_agent (JS)     │
    │     └─ capture .bin per msg      │        │            └─ Cute.Http hooks  │
    │                                  │        │                                │
    └──────────────────────────────────┘        └────────────────────────────────┘
                  │                                             ▲
                  │ trigger: touch /tmp/uma_capture_go           │ response msgpack
                  │         OR stdin ENTER                       │ (plaintext after
                  ▼                                              │  LZ4 decompress)
         data/packet_captures/session_YYYYMMDD_hhmmss/
           ├─ index.jsonl          (one line per capture)
           └─ NNNNNN_<slot>_{in,out}.bin
```

Only the device's `libgadget.so` and the Frida probe are Uma-specific;
everything else is ordinary adb tooling.

## Device-side components

- **ZygiskFrida module** at `/data/local/tmp/re.zyg.fri/`.
  Loaded by Magisk/Zygisk into every zygote child, so it's inside
  `com.cygames.umamusume` from `main()` — no spawn race.
- **`libgadget.so`** is Frida's gadget binary. It exposes port 27042
  on the device and waits for RPC. The host forwards `tcp:27042` over adb.
- **Patched identifiers.** The stock gadget leaks its identity through
  multiple external-visible `/proc` surfaces. We ship a binary-patched
  gadget with fixed-length replacements for the externally visible
  strings:

  | Original            | Patched              | Visibility                    |
  |---------------------|----------------------|-------------------------------|
  | `gum-js-loop`       | `gpu-js-pool`        | `/proc/<pid>/task/*/comm`     |
  | `gmain`             | `mmain`             | same                          |
  | `gdbus`             | `mdbus`             | same                          |
  | `frida-gadget`      | `hidra-widget`      | same + memory                 |
  | `frida-gadget-tcp-%u` | `hidra-widget-tcp-%u` | `/proc/net/tcp` (ephemeral) |
  | `frida-gadget-unix` | `hidra-widget-unix` | `/proc/net/unix` abstract NS  |
  | `frida-context`     | `hidra-context`     | memory only                   |
  | `frida-generate-certificate` | `hidra-generate-certificate` | memory |
  | `frida-error-quark` | `hidra-error-quark` | memory                        |
  | "Emulated realm is not supported by **frida-gadget**" | …by **hidra-widget** | runtime string |

  Replacement string length matches the original to the byte so `.rodata`
  offsets and any PC-relative loads remain valid. See
  `scripts/patch_gadget_strings.py` for the full set plus a reproducible
  patcher:

  ```
  .venv/bin/python scripts/patch_gadget_strings.py \
      /tmp/gadget.orig.so /tmp/gadget.patched.so
  ```

  The other ~850 internal `Frida.<Class>.<method>` strings are left
  untouched — they're only visible via full-memory scan and replacing
  them risks breaking Frida's reflective symbol resolution.

- **Device file layout:**

  ```
  /data/local/tmp/re.zyg.fri/
    ├─ libgadget.so        (patched, in use)
    ├─ libgadget.so.bak    (original, recovery)
    ├─ libgadget32.so      (unused, 32-bit path; not patched yet)
    ├─ libgadget.config.so (compiled config)
    ├─ gadget.js           (boot script)
    └─ config.json
  ```

## Probe / agent stack

Driver: `scripts/frida_c1_probe.py`. Does the whole
start-game → attach → rename-threads → sentinel-check →
(arm | auto-install) → watch → detach cycle.

1. **Launch or no-launch.** Default: `am force-stop` + `monkey -p …`. In
   live sessions pass `--no-launch` — the probe then polls for an
   already-running main pid whose `/proc/<pid>/cmdline` matches the
   package name exactly (rejects helper sub-processes).
2. **Remote device + attach.** Host runs `adb forward tcp:27042`, polls
   `frida.get_device_manager().add_remote_device("127.0.0.1:27042")`
   until gadget answers, then attaches to the pid whose `Process.id`
   matches the main pid. Cold-recovery loop handles the case where only
   a helper fork has registered by the time we look.
3. **Thread rename (before hooks).** An inline script walks
   `/proc/self/task/*/comm`, matches `^(gum-|gmain$|gdbus$|pool-frida|pool-spawn|pool-gum-js|frida-)`
   and overwrites each with `Thread-JVM-<i>`. Four sweeps: initial,
   +200 ms, +1 s, +3 s. Catches threads that get spawned lazily after
   agent load.
4. **Sentinel.** Reads `/proc/self/cmdline` and
   `Process.findModuleByName("libil2cpp.so")` inside the target. If
   cmdline ≠ package name or libil2cpp is missing, detach and fail —
   keeps us from wedging hooks into the wrong process.
5. **Agent load.** Full `frida_agent/dist/agent.js` via
   `session.create_script`. This itself does not install any
   `Interceptor.attach` on IL2CPP code — discovery and hooks are
   gated on explicit RPC calls from the driver.
6. **Phase gate (NEW).** When `--capture-cute-http` is set, the driver
   does **not** fire `captureCuteHttpDelegates` immediately. Instead it
   arms two trigger sources (see below) and blocks the main loop until
   one fires.

## Capture trigger — default-deferred

Context: any `Interceptor.attach` inside `libil2cpp.so` leaves inline
trampolines in managed-code memory. While those trampolines exist, a
runtime integrity scan could detect them. The login / auth handshake
happens at process start, before the user has opted in to any risk. We
defer hook install so the hookable window is as narrow as possible.

**Default behavior with `--capture-cute-http`:**

```
t=0     agent loaded, thread rename sweep
t=0     sentinel passes → attach confirmed to Uma main pid
t=2s    ARMED banner printed; blocks on trigger
        (no hooks yet; minimal footprint beyond gadget itself)
t=Tu    user signals via one of:
          - press ENTER in the probe terminal (stdin.isatty() guard)
          - touch /tmp/uma_capture_go        (overridable via --capture-trigger-file)
t=Tu    captureCuteHttpDelegates(snap) fires:
          - hooks set_DecompressFunc / set_CompressFunc (delegate setters)
          - reads current Instance delegates, hooks their method_ptrs
t=Tu .. t=Tu + --duration
        hooks emit cute_http_codec_{in,out} messages; driver writes .bin
t=end   session.detach()
```

Override: `--capture-auto` restores pre-hardening behavior (install
immediately after sentinel).

**Why two trigger sources?**

- `touch <file>` is the automation-friendly path; lets a script fire the
  hooks without needing a tty attached.
- Stdin ENTER is the interactive path. Guarded by `sys.stdin.isatty()`
  because a backgrounded shell closes stdin EOF, which would otherwise
  auto-fire the moment the probe arms.

## Capture output format

One directory per run, under `data/packet_captures/session_<ts>/`:

- **`index.jsonl`** — one line per `.bin`:
  ```json
  {"t": 47.210, "seq": 12, "slot": "decompress_initial",
   "dir": "out", "len": 20263, "sent": 20263, "truncated": false,
   "file": "000012_decompress_initial_out.bin"}
  ```
  `seq` is monotonic across the session. `slot` identifies which of the
  four hooked delegates emitted the buffer. `sent` < `len` when
  `--capture-cute-http-snap` clipped the buffer.
- **`NNNNNN_<slot>_{in,out}.bin`** — raw `byte[]` payload, plaintext
  msgpack (response: `out`, request: `in`). Offline decode:
  ```
  .venv/bin/python scripts/decode_uma_capture.py data/packet_captures/session_*
  ```

## Known holes

- **`libgadget32.so`** is present on device but unused by the 64-bit
  Uma process. If a 32-bit fork ever mattered, it would still leak the
  stock identifiers — patcher supports it, we just haven't run it.
- **Internal `Frida.*` strings** are still present in the mapped gadget
  file and in RAM. A full-memory scan for `Frida.` or the Frida
  GUID pattern would still flag us. Accepted risk: we weigh this
  against the breakage probability of touching Frida's own
  reflection paths.
- **IPC is file-based.** `auto_turn.py` integration will tail
  `index.jsonl` + lazy-decode; see
  `docs/packet_state_layer.md#ipc-between-probe-and-bot` for the
  alternative socket path.
- **Pre-attach traffic is unreachable.** Anything Uma sends before the
  trigger fires (login, bootstrap, asset manifest) is not captured.
  Home-screen state onward is all we care about for the bot.

## Recovery / rollback

Rollback the gadget to stock (e.g. for isolating whether the patch
broke something):

```
# from host with a running adb device
adb shell /data/local/tmp/su -c \
    'cp /data/local/tmp/re.zyg.fri/libgadget.so.bak \
        /data/local/tmp/re.zyg.fri/libgadget.so'
```

Then force-stop + relaunch Uma so Zygisk reloads the gadget. (The
`/data/local/tmp/su` path exists because `/system/bin/su` is stashed as
`su.bak` to hide root from Uma's own tamper check; a symlink at
`/data/local/tmp/su` invokes the BusyBox-style binary with the right
argv[0].)

Re-deploy the patched gadget after a fresh pull:

```
.venv/bin/python scripts/patch_gadget_strings.py \
    /tmp/libgadget.orig.so /tmp/libgadget.patched.so

adb push /tmp/libgadget.patched.so /data/local/tmp/libgadget.patched.so
adb shell /data/local/tmp/su -c \
    'cp /data/local/tmp/libgadget.patched.so \
        /data/local/tmp/re.zyg.fri/libgadget.so'
adb shell rm /data/local/tmp/libgadget.patched.so
```

## References

- Hook implementation: `frida_agent/src/hook_deserializer.ts`
- Probe driver: `scripts/frida_c1_probe.py`
- Gadget patcher: `scripts/patch_gadget_strings.py`
- Offline decoder: `scripts/decode_uma_capture.py`
- State layer consuming captures: `docs/packet_state_layer.md`
- Schema details: `docs/PACKET_INTERCEPTION_WS3_REPORT.md`
- How we got here (research trail): `docs/PACKET_INTERCEPTION_SPEC_ADDENDUM_4.md`
