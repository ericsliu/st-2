# Bare Magisk baseline test (2026-04-18)

## Why this test

Hachimi-on-BlueStacks pivot is stuck: with Hachimi enabled, Uma exits
cleanly (exit 0) ~0.7s after "Hooking finished" is logged. We had been
assuming this is HyperTech CrackProof detecting Hachimi's il2cpp
trampolines. Before investing more effort in the shield-bypass angle,
we need to confirm that the emulator's environment itself lets Uma
run end-to-end. Otherwise the shield bypass is chasing a red herring.

## Test configuration

At test start:
- Hachimi Magisk module: **disabled** (`/data/adb/modules/hachimi/disable` touched)
- ZygiskFrida Magisk module: **disabled** (`/data/adb/modules/zygiskfrida/disable` touched)
- Magisk Delta (Kitsune, `io.github.huskydg.magisk`) package: **disabled** at OS level
- `/system/bin/su`: **present** (not stashed)
- `/system/xbin/su`: **absent**
- `adb devices`: `127.0.0.1:5555 device`

## Expected outcomes

- **PASS (title/lobby screen loads)**: Magisk + Zygisk + disabled modules is
  benign to Uma. The existing Hachimi crash is entirely about Hachimi /
  shield interaction. Focus returns to shield bypass.
- **FAIL ("not permitted to play")**: Root is still detected even with the
  package + modules disabled. Need to stash `/system/bin/su` and/or kill
  additional Magisk-related artifacts before Hachimi can be meaningful.
- **FAIL (Uma dies clean exit 0 like Hachimi case)**: Something in the
  Magisk/Zygisk environment itself is flagged. Need to isolate further
  (e.g., disable Zygisk entirely).

## Execution plan

1. Disable both modules (done).
2. Issue `adb reboot` to get a clean Zygote that reads the new disable
   flags. adb reboot doesn't actually restart BlueStacks — user needs
   to manually restart the emulator window.
3. After BlueStacks comes back up, launch Uma via `monkey`.
4. Screenshot every few seconds while it boots to detect the failure
   mode (title / not-permitted / crash).
5. If Uma reaches lobby, let it idle ~60s to confirm no delayed kill.

## Results

### Phase A: both modules disabled, `/system/bin/su` present (symlink → ./magisk)

- Uma launches, stays alive (pid 3267 seen from t+0 through title-card splash),
  but **immediately shows "You are not permitted to play on this device."**
  error modal overlaid on the splash background.
- Screenshot: `screenshots/baremagisk_t5.png`, `baremagisk_t13.png`.
- Logcat: Unity logs `ApplicationInfo com.cygames.umamusume version 1.20.17`
  at t+2.4s after `am start`. No crash, no exit — process idles on the
  blocker screen.

**Conclusion:** bare Magisk (even with Kitsune package + all modules
disabled) is NOT benign. Uma's root check still fires because
`/system/bin/su` is a Magisk-provided symlink in the `magisk` tmpfs
overlay at `/system/bin` and is easily visible via `access()` or
`stat()`.

### Phase B: removed `/system/bin/su` symlink from tmpfs

Steps:
```
su -c 'mount -o remount,rw /system/bin'
su -c 'rm /system/bin/su'
su -c 'mount -o remount,ro /system/bin'
```

After this:
- `/system/bin/su` — absent
- `/system/xbin/su` — absent (was already gone from earlier work)
- Hachimi + ZygiskFrida — still disabled
- Magisk Delta (`io.github.huskydg.magisk`) — still disabled at pkg level

Relaunched Uma. At t+8s: **title screen reached.** "Tap to start" button
visible, version 1.20.17 banner, Trainer ID prompt. No "not permitted"
modal. Screenshot: `screenshots/baremagisk_sustash_t8.png`.

Confirms that the root-detect bypass is specifically about removing
`/system/bin/su` — not Kitsune package state or Zygisk-module presence.

### Phase C: stability idle — PASS

Monitor polled every 1s for 60s. Uma pid 4039 stayed alive the entire
window. Screenshot at t+60s shows the title screen animation still
playing ("Tap to start" button, different splash frame → confirms
the UI is active, not frozen). `screenshots/baremagisk_sustash_t50.png`.

No delayed root-check or anti-cheat kill fires with the module set
disabled. The clean shutdown seen with Hachimi enabled is therefore
**specifically attributable to Hachimi's presence**, not a timed
check in the base game.

## Takeaways

1. Uma's first-line root check reads `/system/bin/su` directly. Stashing
   the file (or removing the symlink from the Magisk tmpfs overlay)
   passes this check.
2. The "not permitted to play" screen is Uma-side, not Zygisk/Magisk-
   related — disabling all Magisk modules did not clear it.
3. With `/system/bin/su` gone + both modules disabled, Uma reaches
   title. This is the clean baseline the Hachimi bring-up needed to
   compare against.
4. **Implication for Hachimi crash:** since plain Magisk + disabled
   modules now runs Uma to title cleanly, the clean `exit(0)` observed
   with Hachimi enabled is specifically caused by Hachimi's presence
   (il2cpp hook install, module load, or shield-detection thereof) —
   not by the Magisk environment or `/system/bin/su`. Shield bypass
   via /proc/pid/mem remains the right next investigation.

## Persistent setup note

The `/system/bin/su` removal lives in the Magisk tmpfs overlay, which
is rebuilt on each boot. After any reboot, this command has to be
re-run before launching Uma. Consider automating via a Magisk
`post-fs-data` hook if this becomes routine.

