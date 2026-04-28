# YT Grab — File Inventory & Uninstaller Audit

Static audit of every location YT Grab writes to during install + runtime, and whether the bundled uninstaller (`YTGrabUninstaller.exe`) cleans each one.

Audited from source: `installer.py`, `server.py`, `uninstaller.py`, `YTGrab.spec`. **Not** verified by a live install/uninstall run — the test machine is a Linux sandbox. Items marked ❌ should be confirmed on Windows by clean-install + uninstall + leftover-check.

Legend:
- ✓ — uninstaller wipes it cleanly
- 🟡 PROMPT — uninstaller offers an opt-in action (currently: opt-in export to Desktop before delete)
- 🟢 KEEP — intentional user content the uninstaller won't touch
- ❌ — leftover; not handled by the uninstaller

---

## Install-time paths (written by `YTGrab.exe` first-run install)

| Path | Purpose | Removed? |
|---|---|---|
| `%LOCALAPPDATA%\Programs\YTGrab\` | Install root | ✓ (whole tree wiped via PowerShell `Remove-Item -Recurse`) |
| `%LOCALAPPDATA%\Programs\YTGrab\YTGrab.exe` | The app itself (self-copied from Downloads) | ✓ |
| `%LOCALAPPDATA%\Programs\YTGrab\YTGrabUninstaller.exe` | Bundled uninstaller (downloaded from GitHub release on first install) | ✓ — but the uninstaller is *running* during this delete; PowerShell self-delete handles it via the same retry loop the install dir uses |
| `%LOCALAPPDATA%\Programs\YTGrab\version.txt` | Recorded installed version, drives auto-update check | ✓ |
| `%USERPROFILE%\Desktop\YT Grab.lnk` | App shortcut | ✓ |
| `%USERPROFILE%\Desktop\Uninstall YT Grab.lnk` | Uninstaller shortcut | ✓ |
| `%APPDATA%\Microsoft\Windows\Start Menu\Programs\YT Grab.lnk` | Start Menu app shortcut | ✓ |
| `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Uninstall YT Grab.lnk` | Start Menu uninstall shortcut | ✓ |
| Legacy: `%USERPROFILE%\Desktop\YT Downloader.lnk` | Pre-1.9 shortcut name | ✓ (uninstaller's SHORTCUTS list keeps the legacy name for compat) |
| Legacy: Start Menu `YT Downloader.lnk` | Pre-1.9 Start Menu name | ✓ |

### Not written at install time (verified)

| Path | Why this is correct |
|---|---|
| Registry: `HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\YTGrab` | **The app does NOT register with Windows "Apps & Features".** No Programs & Features entry exists. Users can ONLY uninstall via the shortcut or by directly running `YTGrabUninstaller.exe`. **❌ This is a UX bug** — see Issues below. |
| `%PROGRAMDATA%\` | No system-wide writes |
| File associations (`.youtube-dl.exe`, `.mp4`, etc) | None |
| Autostart (`Run`, `RunOnce`, Startup folder) | None |
| Scheduled Tasks | None |

---

## Runtime paths (written when the installed app runs)

All under the install dir unless otherwise noted. Drained by the same `Remove-Item -Recurse` that nukes the install root.

| Path | Purpose | Removed? |
|---|---|---|
| `%LOCALAPPDATA%\Programs\YTGrab\downloads\` | Default download folder. Per-video subfolders containing `.mp4` + sidecar `.txt` / `.jpg` / `.srt`. | 🟡 PROMPT — uninstaller offers opt-in "Export to Desktop" before delete. After export (or skip), the folder gets wiped with the install root. |
| `%LOCALAPPDATA%\Programs\YTGrab\downloads\<title>\<title>.mp4` | Individual downloads | 🟡 PROMPT (same as above) |
| `%LOCALAPPDATA%\Programs\YTGrab\trash\` | Soft-delete archive (Downloads X moves folder here; Clear Trash sends to Recycle Bin) | 🟡 PROMPT (covered by export step) |
| Legacy: `%LOCALAPPDATA%\Programs\YTGrab\previously_deleted\` | Interim 1.19 name for the trash folder | ✓ (migrated to `trash/` on launch; gets wiped either way) |
| Legacy: `%LOCALAPPDATA%\Programs\YTGrab\previous_downloads\` | Pre-1.19 name for the trash folder | ✓ (same migration) |
| `%LOCALAPPDATA%\Programs\YTGrab\history.json` | Persisted list of completed downloads | 🟡 PROMPT (export covers it) |
| `%LOCALAPPDATA%\Programs\YTGrab\activity.json` | Log of every download (including deleted) | 🟡 PROMPT (export covers it) |
| `%LOCALAPPDATA%\Programs\YTGrab\transcode-debug.log` | ffmpeg encoder debug log | ✓ |
| `%LOCALAPPDATA%\Programs\YTGrab\YTGrab.exe.old` / `.new` | Self-update leftovers (cleaned each launch but may persist if launch crashes) | ✓ (covered by install-root wipe) |
| `%LOCALAPPDATA%\YTGrab\` | **Separate** user-data root (NOT under `Programs\`). Created by the app at runtime. | ✓ |
| `%LOCALAPPDATA%\YTGrab\webview\` | WebView2 user-data dir: localStorage (settings/themes/saved views), IndexedDB, service worker cache | ✓ — `_wipe_webview_cache()` does `shutil.rmtree(LOCALAPPDATA / "YTGrab")` |
| `%TEMP%\_MEI<random>\` | PyInstaller onefile unpack dir. ffmpeg.exe + ffprobe.exe + index.html extracted here per-launch. Normally self-cleans on exit; lingers after a crash. | ✓ — `_wipe_webview_cache()` also sweeps any `_MEI*` under `%TEMP%` |
| `%TEMP%\ytgrab-uninst.log` | Uninstaller's own self-delete log | ❌ Left intentionally (debug breadcrumb in case the install-root wipe partially fails). Tiny file (<5KB). |

### Not written at runtime (verified)

| Path | Why |
|---|---|
| `%USERPROFILE%\.cache\yt-dlp\` | yt-dlp's default metadata cache. **Not explicitly disabled in our opts.** ❌ Likely created during downloads, not cleaned by uninstaller. ~5-50MB. See Issues. |
| `~\.imageio\` / `imageio_ffmpeg` cache | Only used in dev mode (source-run). Packaged YTGrab.exe uses bundled `bin\ffmpeg.exe`, not imageio's. |
| `comtypes\gen` cache | Only in dev mode (venv). Not in packaged build. |
| Settings file on disk | Settings live in WebView2's localStorage at `%LOCALAPPDATA%\YTGrab\webview\` — covered by webview wipe. No `settings.json` on disk. |
| Crash dumps | Python doesn't write them; we don't catch + log them. Windows Error Reporting may write to `%LOCALAPPDATA%\CrashDumps\` if app hard-crashes — uninstaller doesn't touch (and shouldn't, that's WER's territory). |
| Cookies / session data | Not used. App makes no authenticated requests. |
| Update check files | Update state lives in `version.txt` (covered) and is also fetched fresh from GitHub each launch — no cache file. |

---

## Registry entries

| Key | Purpose | Removed? |
|---|---|---|
| *(none)* | App makes zero registry writes | N/A |

The app does NOT touch the registry at all. No `HKCU` writes, no `HKLM` writes, no Apps & Features registration, no file associations, no Run keys. Confirmed by grep across `installer.py`, `server.py`, `uninstaller.py`: no `winreg` import, no `reg add`, no Windows API registry calls.

---

## Issues found in this audit

### ❌ Issue 1 — No Programs & Features entry

**What's missing:** the app doesn't register itself in `HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\YTGrab` (or HKCU equivalent), so it doesn't appear in Windows "Apps & Features" / "Add or Remove Programs". Users can only uninstall via the shortcut or by manually running `YTGrabUninstaller.exe`.

**Risk:** if the user deletes the Uninstall shortcut by accident, there's no obvious way to remove the app. They'd have to find `%LOCALAPPDATA%\Programs\YTGrab\YTGrabUninstaller.exe` themselves.

**Fix:** add a small `_register_uninstaller()` step in `installer.py`'s `_do_install_steps()` that writes the standard ARP keys via `winreg`:

```python
HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\YTGrab
  DisplayName        = "YT Grab"
  DisplayIcon        = <install_dir>\YTGrab.exe,0
  DisplayVersion     = <version.txt contents>
  Publisher          = "SleepyDev"
  InstallLocation    = <install_dir>
  UninstallString    = <install_dir>\YTGrabUninstaller.exe
  EstimatedSize      = <kb>
  NoModify           = 1
  NoRepair           = 1
```

HKCU is correct (per-user install, no admin needed). Then uninstaller adds a matching `winreg.DeleteKey` step.

### ❌ Issue 2 — yt-dlp cache leftover

**What's missing:** yt-dlp creates `~\.cache\yt-dlp\` on first download to cache extractor metadata. This survives uninstall.

**Risk:** harmless (just metadata, no user content), but it's a leftover and contributes to "I uninstalled but YTGrab files are still on my disk" perception. Typical size 5-50 MB.

**Fix options:**
- a) Pass `cachedir=False` to yt-dlp opts in `_yt_opts_base()` — disables the cache entirely. Slight perf hit on subsequent downloads of the same site.
- b) Set `cachedir` to a path inside `%LOCALAPPDATA%\YTGrab\` — the webview wipe then catches it.
- c) Add `~\.cache\yt-dlp` to the uninstaller's wipe list.

Option (b) is cleanest — keeps everything yt-dlp does inside our own data dir.

### ✓ Issue 3 — RESOLVED (false positive in original audit)

The "Export to Desktop" checkbox is already defaulted to ON. `uninstaller.py:405` reads `tk.IntVar(value=1)` (1 = checked). I misread the code in the original pass. No fix needed.

### Edge case — `%TEMP%\ytgrab-uninst.log` left behind

The uninstaller's own self-delete script writes its progress log to `%TEMP%\ytgrab-uninst.log`. This is intentional (so if install-root wipe fails, the user has a record of what was tried) but technically a file the user might consider a leftover. Tiny (<5KB) and `%TEMP%` is auto-cleaned by Windows Disk Cleanup, so I'd call this acceptable.

---

## Live-test plan (Windows-side, before next ship)

For each of the ❌ items above + a complete walkthrough:

```powershell
# 1. Snapshot all relevant locations BEFORE install
Get-ChildItem $env:LOCALAPPDATA\Programs\YTGrab -ErrorAction SilentlyContinue
Get-ChildItem $env:LOCALAPPDATA\YTGrab -ErrorAction SilentlyContinue
Get-ChildItem "$env:USERPROFILE\Desktop\YT Grab.lnk" -ErrorAction SilentlyContinue
Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\YT Grab.lnk" -ErrorAction SilentlyContinue
Get-ChildItem "$env:USERPROFILE\.cache\yt-dlp" -ErrorAction SilentlyContinue
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\YTGrab" 2>$null

# 2. Install: copy fresh dist\YTGrab.exe to Desktop, double-click

# 3. Run the app: download 2-3 videos, change accent color (writes to webview localStorage)

# 4. Snapshot AFTER install + use:
Get-ChildItem $env:LOCALAPPDATA\Programs\YTGrab -Recurse | Select FullName, Length
Get-ChildItem $env:LOCALAPPDATA\YTGrab -Recurse | Select FullName, Length
# ...same for shortcuts, .cache, registry

# 5. Uninstall via the shortcut (don't check the export box -- testing default behavior)

# 6. Wait 10 seconds for self-delete PowerShell script to complete

# 7. Snapshot AFTER uninstall: same commands as step 1.
```

Anything from step 7 that wasn't there in step 1 is a leftover. Expected leftovers given the issues above: `%USERPROFILE%\.cache\yt-dlp\`, `%TEMP%\ytgrab-uninst.log` (acceptable). Anything else is a bug.

---

## Changelog of inventory

This doc tracks file/registry surface area as the app evolves. When a new version writes to a new path, add a row here AND update the uninstaller in the same PR. Don't ship a release that creates a new write location without first proving the uninstaller cleans it.

- v1.20 (planned): no new paths beyond what's listed above.
- Future: if we ever add a "remember last download folder" preference saved outside webview localStorage, add the location here.
