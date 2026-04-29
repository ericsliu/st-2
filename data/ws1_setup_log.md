# WS-1 Setup Log (main-agent takeover)

Background: three sub-agent attempts were blocked by file-write permission denials even after settings allowlist updates and Claude Code restart. Sub-agent permission model apparently bypasses `.claude/settings.local.json`. Pivoted to main-agent execution where Write permissions work.

## Environment snapshot (t=0)

- Host: macOS Darwin 23.3.0, Apple Silicon M1
- MuMuPlayer macOS running
- ADB: 127.0.0.1:5555, already connected
- Uma Musume Global installed as `com.cygames.umamusume`
  - Base APK: `/data/app/~~2dD3Jo6vMhgpbL2wveD_cQ==/com.cygames.umamusume-BppgNemuLspU7LBeIl2_TQ==/base.apk`
  - Split APK (ARM64-only): same dir, `split_config.arm64_v8a.apk`
- URL scheme: `umamusume-en` (Global) vs `umamusume-jp` (JP)
- JP variant `jp.co.cygames.umamusume` NOT installed

## Host tool inventory

Already present:
- `java`, `keytool`, `jarsigner`, `unzip` (all `/usr/bin/`)
- `adb` (Homebrew `android-platform-tools`)
- `curl`, `brew`

Missing (need install):
- `apktool` — APK decompile/rebuild
- `apksigner` — Android v2+ signature (part of Android SDK build-tools)
- `zipalign` — align APK before signing

Candidate sources: `brew install apktool`, and `brew install --cask android-commandlinetools` for apksigner/zipalign.

## Actions log

(entries appended below as work progresses)
