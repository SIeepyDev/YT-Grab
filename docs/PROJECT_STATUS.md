# YT Grab — Project Status (snapshot)

This is the full state of the project as of the v1.20.0 release + the in-flight v1.20.1 patch. Read this top-to-bottom before deciding what to change next. No summaries, only what's actually here.

---

## 1. Architecture overview

### File-by-file rundown

| File | Purpose | Size |
|---|---|---|
| `server.py` | The Flask + pywebview app. Download pipeline (yt-dlp), transcode pipeline (ffmpeg), history/trash management, Windows shortcut + taskbar branding, Explorer-window single-instance reuse, in-app context menus, theme persistence. **Entry point of the running app.** | 3,960 lines |
| `installer.py` | Bootstrap helper imported by `server.py` at startup. Detects fresh-download vs installed-launch. On fresh: self-copies to `%LOCALAPPDATA%\Programs\YTGrab\`, extracts the bundled `YTGrabUninstaller.exe`, creates Desktop + Start Menu shortcuts, registers with Apps & Features, launches the installed copy. On installed-launch: silent GitHub update check + rename-swap if newer version exists. | 789 lines |
| `uninstaller.py` | Standalone tkinter uninstaller. Optional export, kill processes, close Explorer windows on install dir, remove shortcuts, wipe webview/yt-dlp caches, unregister from Apps & Features (with recursive subkey delete + diagnostic logging), schedule install-dir self-delete via PowerShell. | 707 lines |
| `index.html` | Entire UI — HTML + CSS + JS in one file by design. Three-column workspace (Downloads / paste form + Previously Deleted / Active queue), color picker with Liquid Glass aesthetic, theme system (background/gradient/typography/glass/views), keyboard shortcuts, command palette (Ctrl+K), context menus, auto-update banner. | 5,859 lines |
| `YTGrab.spec` | PyInstaller spec for the public `YTGrab.exe`. Bundles `index.html`, `icon.ico`, ffmpeg+ffprobe from `bin/`, imageio_ffmpeg, pywebview, yt_dlp's extractor submodules, `dist/YTGrabUninstaller.exe`, and `.release-please-manifest.json` as data resources. Outputs `dist\YTGrab.exe`, ~190 MB. Fails the build if the manifest or uninstaller is missing. |
| `Uninstaller.spec` | PyInstaller spec for `YTGrabUninstaller.exe`. Stdlib-only (tkinter), aggressively excludes everything else. Outputs `dist\YTGrabUninstaller.exe`, ~10 MB. |
| `build.bat` | Build script. Order matters: builds `YTGrabUninstaller.exe` FIRST so `YTGrab.spec` can bundle it. Installs `pyinstaller` + `pillow` (build_requirements.txt) + the runtime requirements, regenerates `icon.ico` from `icon.png`, fetches ffmpeg if missing, runs both PyInstaller passes. |
| `release.bat` | Local-only publisher. Reads version from `.release-please-manifest.json`, hands off to `release.ps1` which uploads `dist\YTGrab.exe` + `dist\YTGrabUninstaller.exe` to the matching GitHub release tag. Has diagnostic block (CWD + dist listing) before the existence check to surface CWD bugs at the source. |
| `release.ps1` | Does the actual GitHub API work — find or create the release, delete conflicting assets, upload via `uploads.github.com`. Requires `$env:GH_PAT`. |
| `package.bat` | Wraps `dist\YTGrab.exe` + a source-mode fallback into `dist\YTGrab.zip` for sending directly to friends (alternative to GitHub release). |
| `clean.bat` | Wipes `venv/`, `build/`, `dist/`, `__pycache__/`, `icon.ico` (only if `icon.png` exists). Doesn't touch `bin/` (ffmpeg) or user data. |
| `fetch_ffmpeg.bat` | Downloads yt-dlp's official ffmpeg+ffprobe build (`ffmpeg-master-latest-win64-gpl.zip` from `github.com/yt-dlp/FFmpeg-Builds`) into `bin/`. Idempotent. |
| `build_icon.py` | Converts `icon.png` to a multi-size `icon.ico` (16/32/48/64/128/256). Skipped if no PNG present or if the ICO is already newer than the PNG. |
| `launch.bat` | Verbose source-mode launcher. Creates the venv on first run, installs requirements, launches `pythonw server.py`. |
| `launch.vbs` | Silent source-mode launcher. Delegates to `launch.bat` on first run (so user sees pip progress); subsequent runs spawn `pythonw.exe server.py` detached. Default entry point for the source-clone install path. |
| `.release-please-manifest.json` | Single source of truth for the version. Currently `{".":  "1.20.0"}`. Bumped automatically by release-please on PR merge. Bundled into `YTGrab.exe` so installer.py's `_read_app_version()` can read it at runtime. |
| `release-please-config.json` | Tells release-please how to bump (Python release type, conventional commits, changelog sections). |
| `.github/workflows/release-please.yml` | Single workflow. On every push to main, runs `googleapis/release-please-action@v4`. Opens or updates a single release PR; merge → tag + GitHub Release. |
| `CHANGELOG.md` | Auto-managed by release-please. Currently top entry is v1.20.0 (2026-04-28). |
| `requirements.txt` | Runtime deps: flask, yt-dlp, youtube-transcript-api, imageio-ffmpeg, send2trash, pywebview, comtypes. |
| `build_requirements.txt` | Build-only deps: pyinstaller, pillow. |
| `bin/` | (gitignored) Holds `ffmpeg.exe` + `ffprobe.exe`, ~400 MB combined. Fetched by `fetch_ffmpeg.bat`. PyInstaller bundles them into `YTGrab.exe` at build time. |
| `screenshots/` | README assets (hero, layout, video-card, history-panel, active-queue, sidebar-left, sidebar-right, settings-detail). |
| `docs/FILE_INVENTORY.md` | Static audit of every install/runtime path with uninstaller coverage. Created in v1.20.0 audit pass. |
| `docs/PROJECT_STATUS.md` | This file. |
| `CLAUDE.md` | Notes for the AI session — commit format, ship flow, version source-of-truth. |
| `CONTRIBUTING.md` | Bug + PR guidance. Solo project, scope is "local-first Windows-only YouTube downloader." |
| `LICENSE` | 0BSD (effectively public domain, attribution-optional). |
| `README.md` | Public-facing intro, install instructions, build instructions, architecture diagram. |

### Entry points

```
End-user-facing:
  YTGrab.exe (PyInstaller frozen output)
    → bootstrap chain: PyInstaller bootloader unpacks _MEIPASS, runs server.py
    → server.py's __main__ block:
        1. installer.bootstrap_or_update()
           - Reads sys.executable; if NOT at INSTALL_DIR/YTGrab.exe:
             → first-install flow (tkinter GUI, self-copy, extract uninstaller,
               shortcuts, registry, launch installed copy, exit)
           - If AT INSTALL_DIR/YTGrab.exe:
             → update check; on newer version: download, rename-swap, relaunch
             → otherwise: fall through and start app
        2. _port_in_use(8765) check (single-instance guard)
        3. _set_app_user_model_id() (Windows taskbar identity)
        4. _ensure_windows_shortcuts() (only relevant in source-mode dev)
        5. Flask in background thread + heartbeat monitor in another thread
        6. _launch_pywebview() on the main thread (pywebview requires it on Windows)

Source-mode (developer):
  launch.vbs / launch.bat
    → venv setup if missing
    → pythonw server.py (server.py runs same __main__ but installer.bootstrap is a no-op
      because sys.frozen is False)
```

### Data flow: URL paste → file on disk

```
User pastes URL into the URL field
  → /api/info POST (yt-dlp extract_info, no download)
    → returns title, channel, thumbnail, available formats
    → UI shows the video card

User clicks Download
  → /api/download POST (params: format_group, format_ext, resolution, audio_bitrate,
                        want_transcript, want_thumbnail, want_subtitles)
  → server starts a background thread:
      _do_download(job_id, url, params):
        1. yt-dlp probe → get title + id → compute unique stem
        2. Create downloads/<stem>/ folder
        3. yt-dlp download with format selector (_fmt_selector based on
           container + resolution) and format_sort override
           (["res", "tbr", "vcodec:vp9", "vcodec:h264"]) so high-bitrate
           VP9 wins over low-bitrate AV1 at matching resolution.
           Postprocessors handle merging, thumbnail embed, metadata embed.
        4. Resolve final filename (yt-dlp may rewrite extension during merge)
        5. (video group only) _transcode_to_ae_friendly(filename, job_id):
           - ffprobe duration + resolution + fps + 4-step bitrate probe
           - target_kbps = max(probed, resolution+fps_default) [floor]
           - maxrate = 1.5 × target, bufsize = 2 × maxrate
           - Encoder selection: NVENC → AMF → QSV → libx264
             (subject to runtime gate _HWACCEL_DISABLED_AT_RUNTIME)
           - All branches: -rc cbr, -b:v target, -minrate target, -maxrate target,
             -bufsize cbr_buf, -pix_fmt yuv420p (or nv12 for QSV)
           - Audio: -c:a aac -b:a 192k
           - -map_metadata -1 (drop source metadata; was producing "Year: 65124")
           - -progress pipe:1 + parse out_time_us → push live percent to job dict
           - On hwaccel failure: recurse with _force_software=True (libx264 veryfast)
           - On final failure: stash ffmpeg's last stderr line in the job's error field
        6. Thumbnail sidecar (.jpg) handling — if user wanted it, keep one
        7. Subtitle sidecar (.srt) — separate yt-dlp pass with --skip-download,
           guarded so subtitle errors don't fail the download
        8. Transcript sidecar (.txt) — fetch_transcript_text via youtube-transcript-api
        9. Mark job done, append to history.json, return final filename via /api/progress_all
        
Output:
  %LOCALAPPDATA%\Programs\YTGrab\downloads\<title> [<id>]\<title> [<id>].mp4
  + optional .txt / .jpg / .srt sidecars in same folder
```

### Where state lives

| Data | Location | Lifetime |
|---|---|---|
| Downloaded videos | `%LOCALAPPDATA%\Programs\YTGrab\downloads\<unique_stem>\` | Until user deletes |
| Soft-deleted videos | `%LOCALAPPDATA%\Programs\YTGrab\trash\` | Until user empties Trash or runs Clear All |
| History (downloads on disk) | `%LOCALAPPDATA%\Programs\YTGrab\history.json` | Synced with the downloads/ folder |
| Activity log (everything ever downloaded, including deleted) | `%LOCALAPPDATA%\Programs\YTGrab\activity.json` | Append-only across sessions |
| Settings (theme, accent, density, a11y toggles, saved views) | WebView2 localStorage at `%LOCALAPPDATA%\YTGrab\webview\` | Survives app updates, wiped only by uninstall |
| Version | `%LOCALAPPDATA%\Programs\YTGrab\version.txt` | Updated on install + on each successful self-update |
| Per-install UUID | `%LOCALAPPDATA%\Programs\YTGrab\install_id.txt` | Generated once on first install, persisted across updates |
| Update leftovers | `<install>\YTGrab.exe.old` and `.new` | Cleaned at every launch by `_cleanup_update_leftovers()` |
| Transcode debug log | `%LOCALAPPDATA%\Programs\YTGrab\transcode-debug.log` | Append-only, includes encoder detection + per-job probe + ffmpeg argv |
| Uninstall log | `%TEMP%\ytgrab-uninst.log` | Written by Python uninstaller and PowerShell self-delete script |

---

## 2. Feature inventory

### Three-column workspace (default layout)

```
┌──────────────────┬─────────────────────────────────┬──────────────────┐
│  Downloads       │  Paste URL → preview → download │  Active queue    │
│  (files on disk) │  + Previously Deleted log       │  (downloads in   │
│                  │                                 │   flight)        │
└──────────────────┴─────────────────────────────────┴──────────────────┘
```

### Left column — Downloads (was "History" pre-1.19)

- Lists every video currently on disk
- Filter input (live filter as you type)
- Per-row: thumbnail tile, title, channel, resolution + bitrate badge, age
- Per-row buttons: open file, view transcript (if exists), delete
- Per-row context menu (right-click): Copy URL, Open on YouTube, Reveal in folder, Rename, Redownload, Convert to AE-friendly (video only), Delete
- Inline rename: F2 / double-click title / pencil button
- "Clear all" button (hits Recycle Bin, undoable via Ctrl+Z)
- Count badge in the header

### Middle column — paste form + preview + Previously Deleted

- URL input field
- Format group toggle: Video / Audio
- Container selector (mp4/mkv/webm for video; mp3/m4a/opus/wav/flac for audio)
- Resolution selector (best/2160/1440/1080/720/480/360 for video; ignored for audio)
- Audio bitrate selector (128/192/256/320 kbps for lossy audio; ignored for lossless)
- Sidecar checkboxes: transcript, thumbnail, subtitles
- Hint line: "Enter to load · Ctrl+V to paste & load · Ctrl+Enter to download · Ctrl+Z to undo delete"
- Below: video card preview (after URL load) — title, channel, duration, thumbnail, available formats summary
- Below preview: Previously Deleted log (was "Previous Downloads" pre-1.19)
  - Lists everything ever downloaded then deleted
  - Per-row: Redownload (one-click), permanently remove
  - Filter input
  - "Clear all" (only clears the log; files were already deleted)

### Right column — Active queue

- One row per in-flight or recently-completed job
- Per-row: title, percent, speed/ETA OR transcoding-status string, progress bar, ✕ Cancel button
- "✓ complete" with auto-fade after 2 seconds when done
- "✗ <error_message>" on failure (now shows the actual ffmpeg stderr line, not just "failed")
- Transcoding status reads "transcoding · 47%" with live percent during ffmpeg phase
- Auto-clears finished jobs from view; the row sticks for 2 seconds for confirmation

### Sidebars

**Left sidebar** — collapsible. Sections:
- Customize → Accent color (HSV ring + SV disc; hex input REMOVED in v1.20)
- Density toggle (compact / comfortable)
- Settings → about, dyslexia-friendly font, high-contrast mode, hide-hints, Reset all settings, Import data, Export data
- Hide-hints toggle pinned at the bottom
- Open trash folder button
- Keyboard shortcuts button

**Right sidebar** — themes drawer. Sections:
- Theme mode buttons (Dark / Light / System)
- Background presets (Dark: Default/Void/Graphite/Midnight/Slate; Light: Default/Cream/Snow)
- Background film-grain toggle
- Gradient mesh presets (Off/Aurora/Sunset/Ocean/Plum/Ember/Mono/Accent)
- Gradient intensity slider
- Gradient animated-drift toggle
- Typography sliders: font size (85–120%), line height (130–170%)
- Font weight: Light / Regular / Medium
- Glass mode: None / Subtle / Medium / Heavy (controls backdrop-filter blur)
- Saved Views: save current settings as a named preset, recall later

### Title bar

- Custom title bar (pywebview frameless window, app drives Win32 ReleaseCapture+WM_NCLBUTTONDOWN drag)
- Brand label
- Theme-mode cycle button (Dark→Light→System with sun/moon/monitor icon swap)
- Min/Max/Close buttons (Win32-correct semantics including drag-to-top maximize)

### Update banner

- On launch, JS hits `api.github.com/repos/SIeepyDev/YT-Grab/releases/latest`
- If `_isNewerVersion(latest, current)` is true, shows a small banner with version + Download link
- 8-second AbortController timeout
- Silent on offline / API rate-limited / blocked
- `_isNewerVersion` strips `-rc1` / `+meta` suffixes before comparing base semver

### Command palette (Ctrl+K)

Searchable list of every action in the app:
- Theme: Dark / Light / System
- Background: 8 presets + grain toggle
- Gradient: 7 presets + drift toggle
- Typography: increase/decrease font size, increase/decrease line height, weight changes, dyslexia toggle
- Glass: 4 modes
- Accessibility: high contrast toggle
- Views: apply named preset, save current view
- Actions: paste URL, load info, open downloads folder, clear Downloads, undo last delete
- Navigate: focus URL input

### Context menus

Downloads-row right-click:
- Copy URL
- Open on YouTube
- Reveal in folder
- Rename (or F2 / double-click title)
- Redownload (re-fires the original URL with the same params)
- Convert to AE-friendly (video only)
- Delete

### File management

- **Individual delete**: per-row ✕ button → soft-delete to trash/, undoable
- **Inline rename**: F2 / double-click title / pencil. Renames folder + media + sidecars together
- **Soft delete + Ctrl+Z undo**: deletes go to `trash/` first; Ctrl+Z restores
- **Hard delete**: clearing the Previously Deleted log sends the trash to the Recycle Bin
- **Clear all**: from Downloads (moves whole list to trash); from Previously Deleted (drops the log + Recycle-Bins the trash)
- **Export**: writes a `YTGrab-export-<timestamp>/` folder to Desktop; user picks Downloads, Previously Deleted, or both
- **Import**: reads a YTGrab-export-* folder, accepts `trash/` (current) AND legacy `previously_deleted/` / `previous_downloads/` folder names
- **Open downloads folder** / **Open trash folder** sidebar shortcuts (each Explorer-window-aware: focuses an existing window if one is already open at that path, doesn't spawn duplicates)

### Theme system

- Dark / Light / System (pulls from `prefers-color-scheme`)
- Background: solid presets (8 dark + 3 light) + optional film grain texture
- Gradient mesh: 7 presets + Accent (uses current accent-rgb) + Off; intensity slider; animated-drift toggle
- Typography: 3 font weights, 4 size steps, 4 line-height steps, dyslexia-friendly font alternate
- Glass: 4 backdrop-blur intensity levels (controls all `--glass-*` CSS vars)
- Accent color: HSV ring + SV disc with Liquid Glass aesthetic (distilled-water + injected-color, two-pool hue bleed underneath, reactive accent overlay on top)
- Saved Views: snapshot all theme settings as named presets, recall instantly

### Keyboard shortcuts

| Key | Action |
|---|---|
| `Enter` (in URL field) | Load preview |
| `Ctrl+V` (in URL field) | Paste URL & auto-load |
| `Ctrl+Enter` (in URL field) | Start download |
| `Ctrl+K` | Open command palette |
| `Ctrl+Z` | Undo last delete |
| `Ctrl+,` | Open settings |
| `F2` | Rename focused Downloads row |
| Double-click row title | Rename |
| `?` | Open keyboard shortcuts cheatsheet |
| `Esc` | Close any open modal / panel |

### Download options

- **Video formats**: mp4, mkv, webm — but as of v1.19+, all video downloads are post-processed into AE-friendly H.264 mp4 regardless of selected container
- **Audio formats**: mp3, m4a, opus, wav, flac (lossless), with bitrate selector for lossy
- **Resolutions**: best (no cap), 2160, 1440, 1080, 720, 480, 360
- **Sidecars**: transcript (.txt via youtube-transcript-api), thumbnail (.jpg), subtitles (.srt via yt-dlp)

---

## 3. Technical pipeline detail

### yt-dlp integration

**Format selector** (`_fmt_selector` in server.py:1183):

```python
if res == "best":
    return "bestvideo+bestaudio/best"
h = {2160, 1440, 1080, 720, 480, 360}[res]
if h <= 1080 and container == "mp4":
    return "bestvideo[height<=H][ext=mp4]+bestaudio[ext=m4a]/" \
           "bestvideo[height<=H]+bestaudio/best[height<=H]/best"
if h <= 1080 and container == "webm":
    return "bestvideo[height<=H][ext=webm]+bestaudio[ext=webm]/..."
# >=1440p OR mkv: codec filter dropped (YouTube only serves H.264 up to 1080p)
return "bestvideo[height<=H]+bestaudio/best[height<=H]/best"
```

**format_sort override** (added v1.20.0):

```python
opts["format_sort"] = ["res", "tbr", "vcodec:vp9", "vcodec:h264"]
```

Same resolution → higher total bitrate wins. VP9 preferred (high-bitrate path on YouTube), H.264 second, AV1 falls last (YouTube's AV1 is bitrate-starved relative to VP9 at the same resolution). This fixes the v1.19 bug where yt-dlp's default ranking picked format 401 (AV1, ~1.8 Mbps) over format 313 (VP9, ~4 Mbps) for the same 4K video.

**Postprocessors** (yt-dlp side):
- `FFmpegThumbnailsConvertor` (webp → jpg, must run before EmbedThumbnail)
- `EmbedThumbnail` (gated on HAS_FFPROBE)
- `FFmpegMetadata` (gated on HAS_FFPROBE)
- `FFmpegExtractAudio` for audio downloads (codec + bitrate as user picked)

**Subtitle handling**: separate yt-dlp invocation with `skip_download=True`, narrow language list (`en`, `en-US`, `en-GB`), `ignoreerrors=True`, all wrapped in try/except so a bad subtitle track can't kill an otherwise-successful video download. yt-dlp's default behavior treats subtitle errors as fatal — this opt-out is intentional.

### ffmpeg pipeline

**Encoder priority + smoketest** (`_detect_hwaccel_encoder` in server.py:441):

```
At module load (once per app start):
  1. Probe `ffmpeg -hide_banner -encoders` for compiled-in codecs
  2. For each candidate in order [h264_nvenc, h264_amf, h264_qsv]:
     - If not in encoder list → mark "NOT_COMPILED", skip
     - If in encoder list → run smoketest:
         ffmpeg -f lavfi -i testsrc=size=256x256:rate=30:duration=0.2 \
                -c:v <codec> -f null NUL
         (256x256 covers NVENC's minimum resolution; 64x64 in v1.19
          silently rejected NVENC on every NVIDIA machine, falling
          through to QSV)
     - Capture pass/fail + stderr tail
  3. Select first PASS as _HWACCEL_ENCODER
  4. Stash full result dict in _ENCODER_DETECTION_INFO
  5. First transcode flushes _ENCODER_DETECTION_INFO to transcode-debug.log:
        [encoders] available  = {'h264_nvenc': True, ...}
        [encoders] smoketest  = {'h264_nvenc': 'PASS', 'h264_amf': 'NOT_COMPILED', ...}
        [encoders] selected   = h264_nvenc
```

**Runtime fallback gate** (`_HWACCEL_DISABLED_AT_RUNTIME`):

If a hwaccel encode fails on real content, this flag flips. Subsequent jobs in the same session skip hwaccel and go straight to libx264 (no repeated failure-and-recover delay per job). Resets on app restart so a driver update can be picked up next launch.

**Encoder commands** (CBR-with-minrate as of v1.20.0; was VBR through v1.19 which let QSV undershoot 60 Mbps targets to 32 Mbps):

```python
# Common base
[ffmpeg, "-y", "-hide_banner", "-loglevel", "warning",
 "-i", in_path,
 "-map", "0:v:0", "-map", "0:a:0?",  # video + optional audio]

# NVENC
["-c:v", "h264_nvenc", "-preset", "medium", "-rc", "cbr",
 "-b:v", f"{target}k", "-minrate", f"{target}k", "-maxrate", f"{target}k",
 "-bufsize", f"{2*target}k", "-pix_fmt", "yuv420p"]

# AMF
["-c:v", "h264_amf", "-rc", "cbr",
 "-b:v", f"{target}k", "-maxrate", f"{target}k",
 "-bufsize", f"{2*target}k", "-pix_fmt", "yuv420p"]

# QSV (no -minrate; -b:v=-maxrate forces CBR-like)
["-c:v", "h264_qsv", "-preset", "medium", "-rc", "cbr",
 "-b:v", f"{target}k", "-maxrate", f"{target}k",
 "-bufsize", f"{2*target}k", "-pix_fmt", "nv12"]

# libx264 software fallback
["-c:v", "libx264", "-preset", "veryfast",
 "-b:v", f"{target}k", "-minrate", f"{target}k", "-maxrate", f"{target}k",
 "-bufsize", f"{2*target}k", "-x264opts", "nal-hrd=cbr",
 "-threads", "4", "-pix_fmt", "yuv420p"]

# Common tail
["-c:a", "aac", "-b:a", "192k",
 "-movflags", "+faststart",
 "-map_metadata", "-1",                 # drop garbage source metadata
 "-progress", "pipe:1", "-nostats",     # progress lines on stdout
 out_path]
```

Process-level: `subprocess.Popen` with `BELOW_NORMAL_PRIORITY_CLASS` (0x4000) so the encoder doesn't fight foreground apps for CPU.

**Bitrate probe chain** (`_video_bitrate_kbps` in server.py:712):

```
Step 1: ffprobe stream=bit_rate          (already video-only → return as-is)
Step 2: ffprobe format=bit_rate          (total → subtract 192 kbps audio)
Step 3: filesize × 8 / duration          (total → subtract 192 kbps audio)
Step 4: resolution+fps default           (already video-only)
Apply floor: final = max(probed, resolution+fps default)
```

The floor is the "AE clean room" guarantee — even a 1.8 Mbps source (like a low-bitrate AV1 4K stream) gets transcoded at the spec'd 4K bitrate so the H.264 encoder has room to NOT add new compression artifacts on top of the source's existing ones.

**Resolution + fps floor table** (RESOLUTION_DEFAULTS_KBPS in server.py):

```
4K (h>=2160):    60000 kbps @ 60fps,  45000 kbps @ 30fps
1440p:           24000 kbps @ 60fps,  18000 kbps @ 30fps
1080p:           18000 kbps @ 60fps,  12000 kbps @ 30fps
720p:             9000 kbps @ 60fps,   6000 kbps @ 30fps
< 720p:           4000 kbps both fps classes
```

Numbers track YouTube's official H.264 upload recommendations at the high end. Cutoff between 30 and 60 is at 31 fps so 29.97 reads as 30 and 59.94 reads as 60 cleanly.

**Output naming**:

```
<install>\downloads\<title> [<youtube_id>]\<title> [<youtube_id>].mp4
                                               + .txt / .jpg / .srt sidecars (optional)
```

`<title>` is whatever yt-dlp's `%(title)s` resolves to (sanitized). `<youtube_id>` is the 11-character video ID. The `_unique_stem()` helper appends ` (2)`, ` (3)` etc. to prevent collisions when the same video is re-downloaded after a delete.

There's no AE-suffix on the output filename — the file IS the AE-friendly version (transcoded in place). The original VP9/AV1 source is overwritten by the H.264 mp4 atomically via a `<stem>.__ae__.mp4` staging name + `Path.replace`.

---

## 4. Install / update / uninstall

### Install flow (running YTGrab.exe from Downloads, fresh)

```
1. installer.bootstrap_or_update() detects am_installed=False
   (sys.executable's path != INSTALL_DIR/YTGrab.exe)
2. _install_from_bundle() runs:
   a. INSTALL_DIR.mkdir
   b. _kill_running_app() — taskkill any running YTGrab.exe (PID-filtered to
      not kill self) + YTGrabApp.exe (legacy v1.17 inner-app) +
      YTGrabSetup.exe (pre-1.17 standalone updater)
   c. _self_copy_into_install_dir() — shutil.copy2 self → INSTALL_DIR/YTGrab.exe
   d. _cleanup_legacy_artifacts() — removes orphan YTGrabSetup.exe / YTGrabApp.exe
      from pre-1.18 layouts
   e. _extract_bundled_uninstaller() — copies YTGrabUninstaller.exe out of
      sys._MEIPASS to INSTALL_DIR. (v1.19 used to download from GitHub here,
      which broke pre-release testing.)
   f. _create_shortcuts() — Desktop + Start Menu .lnks (one for app pointing
      at YTGrab.exe, one for uninstall pointing at YTGrabUninstaller.exe).
      WScript.Shell COM via PowerShell. Idempotent: rebuilds on every install
      (handles user-deleted shortcuts).
   g. APP_VERSION resolved from .release-please-manifest.json bundled in
      sys._MEIPASS (v1.20 fix; was a hardcoded constant before)
   h. version.txt written with APP_VERSION
   i. _register_uninstall_entry(version) — writes
      HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\YTGrab\
      with DisplayName, DisplayVersion, Publisher, InstallLocation,
      UninstallString, DisplayIcon, InstallID (UUID), URLInfoAbout,
      EstimatedSize, NoModify, NoRepair. Per-user (HKCU) so no UAC prompt.
   j. _spawn_installed_copy() — Popen INSTALL_DIR/YTGrab.exe detached
   k. _schedule_source_delete() — fires a hidden PowerShell that waits 3s
      then deletes the Downloads-folder source copy (so the user doesn't
      end up with two copies)
3. Tkinter window self-destroys; the spawned installed copy runs
   bootstrap_or_update() again, this time hits am_installed=True and
   falls through to the normal app launch.
```

### Self-update (installed YTGrab.exe → newer GitHub release)

```
On every launch of installed YTGrab.exe:
  installer._run_installed():
    1. If YTGrabApp.exe is missing (deleted by user, etc.), re-extract from bundle.
       (Holdover from v1.17's bundled-app architecture; now a legacy code path
        since v1.18 doesn't have an inner YTGrabApp.exe.)
    2. _fetch_latest_release() — hits api.github.com/repos/.../releases/latest
       with 8s timeout, silent on failure.
    3. If newer version available:
       _perform_self_update():
         a. Find YTGrab.exe asset in latest release; bail if missing
         b. Download to INSTALL_DIR/YTGrab.exe.new
         c. Rename current YTGrab.exe → YTGrab.exe.old
            (NTFS allows renaming a running .exe but not deleting it)
         d. Rename .new → YTGrab.exe
         e. Update version.txt to the new tag
         f. _register_uninstall_entry(new_version) — refresh registry
         g. Kill any running YTGrabApp.exe (legacy)
         h. Spawn the new YTGrab.exe detached, exit self
       The new YTGrab.exe's first launch hits _run_installed again,
       _cleanup_previous_update_leftover() removes YTGrab.exe.old, falls
       through to normal app launch.
    4. If up-to-date: launch the app (server.py's __main__ continues past
       the bootstrap return).
```

### Uninstall flow (clicking shortcut OR Settings → Apps → Uninstall)

```
Windows runs the registry's UninstallString = INSTALL_DIR/YTGrabUninstaller.exe.
Tkinter window opens. User checks "Export downloads + history to Desktop first"
(default ON), clicks Uninstall.

UninstallerWorker.run():
  1. P_STARTED → if export_first: _export_data() copies downloads/, trash/,
     history.json, activity.json to YTGrab-export-<ts>/ on Desktop
  2. _kill_process() — taskkill YTGrab.exe + legacy names + ffmpeg.exe + ffprobe.exe
  3. _close_explorer_windows() — PowerShell + Shell.Application COM walks open
     Explorer windows, closes any whose LocationURL matches "Programs[/\]YTGrab"
  4. _remove_shortcuts() — unlinks 6 .lnk files (Desktop YT Grab + Uninstall;
     Start Menu YT Grab + Uninstall; legacy "YT Downloader" Desktop + Start Menu)
  5. _wipe_webview_cache() — shutil.rmtree on:
       - %LOCALAPPDATA%\YTGrab\ (the webview cache, NOT the install dir)
       - %TEMP%\_MEI* dirs (PyInstaller leftovers from any crash)
  6. _wipe_yt_dlp_cache() — shutil.rmtree on ~/.cache/yt-dlp (added v1.20)
  7. _unregister_arp_entry() — recursive winreg.DeleteKey on
     HKCU\Software\Microsoft\...\Uninstall\YTGrab\. Logs each step to
     %TEMP%\ytgrab-uninst.log:
       [arp] target = ...
       [arp] removing subkey ... (if any)
       [arp] removed ...
       [arp] final result = OK
  8. _schedule_self_delete() — spawns detached hidden PowerShell that:
       a. Waits 2s for this Python process to exit
       b. Re-kills YTGrab/YTGrabApp/YTGrabSetup/YTGrabUninstaller/ffmpeg/ffprobe
       c. Loops Remove-Item INSTALL_DIR up to 10 times with 1.5s gaps
          (handles slow Explorer handle release)
       d. Belt+suspenders: Remove-Item HKCU\...\Uninstall\YTGrab (in case Python
          step 7 crashed before its registry call)
       e. Logs everything to %TEMP%\ytgrab-uninst.log via Add-Content (NOT
          Set-Content — Set-Content was wiping the [arp] lines from step 7)
  9. Python process exits. PS script continues until SUCCESS or FAILED.
```

### What's bundled vs downloaded

| Artifact | Where it comes from | Why |
|---|---|---|
| `YTGrab.exe` | User downloads from GitHub release | The single public download |
| `YTGrabUninstaller.exe` | (a) bundled inside YTGrab.exe + (b) uploaded as separate release asset | (a) install always succeeds offline; (b) standalone download for safety-net access |
| `.release-please-manifest.json` | Bundled inside YTGrab.exe | APP_VERSION read at runtime, can never desync from the tag |
| `index.html` | Bundled inside YTGrab.exe | The app's UI |
| `icon.ico` | Bundled inside YTGrab.exe | Window/taskbar icon |
| `ffmpeg.exe` + `ffprobe.exe` | Bundled inside YTGrab.exe (via `bin/`) | Transcode + probe; ~190 MB of YTGrab.exe's size is these two |
| WebView2 runtime | Pre-installed on Windows 11; auto-installer on Windows 10+ | pywebview's window backend |
| Latest version metadata | GitHub releases/latest at runtime | Auto-update check |

---

## 5. Build / release infrastructure

### build.bat (in order)

```
1. Verify venv\Scripts\python.exe exists, error if not (run launch.bat first)
2. pip install -r build_requirements.txt  (pyinstaller, pillow)
3. pip install -r requirements.txt        (flask, yt-dlp, etc.)
4. rmdir /s /q build/ + dist/             (clean previous builds)
5. python build_icon.py                   (icon.png → icon.ico, idempotent)
6. fetch_ffmpeg.bat if bin/ffprobe.exe absent  (downloads ~130 MB)
7. PyInstaller --clean Uninstaller.spec   →  dist\YTGrabUninstaller.exe (~10 MB)
8. PyInstaller --clean YTGrab.spec        →  dist\YTGrab.exe (~190 MB,
                                              pulls in dist\YTGrabUninstaller.exe
                                              + .release-please-manifest.json)
9. Print success + open dist\ in Explorer
```

Order in step 7-8 matters: `YTGrab.spec` reads `dist\YTGrabUninstaller.exe` from disk and bundles it as a data file. If you reverse the order, the spec fails the build with a clear error.

### YTGrab.spec — what's bundled

```
hiddenimports: full submodule lists for yt_dlp, yt_dlp.extractor,
               yt_dlp.postprocessor, youtube_transcript_api, webview, comtypes
               + pywebview platform backends (edgechromium, mshtml, winforms)
               + 'installer' (because server.py imports it lazily inside __main__)
datas:         index.html, icon.ico (if present),
               bin/ffmpeg.exe + bin/ffprobe.exe (if present),
               imageio_ffmpeg's bundled binaries,
               webview's JS bridge files,
               dist/YTGrabUninstaller.exe (fails build if missing),
               .release-please-manifest.json (fails build if missing)
excludes:      tkinter is NOT excluded (used by installer.py's GUI);
               matplotlib/numpy/PIL/pandas/scipy/IPython/jupyter/notebook
               /pytest/sphinx are excluded
console:       False (windowless)
upx:           False (UPX makes Defender suspicious)
```

### Uninstaller.spec — what's bundled

```
datas:         empty
hiddenimports: empty (tkinter auto-detected)
excludes:      everything not stdlib (matplotlib, numpy, ..., yt_dlp, flask,
               webview, imageio_ffmpeg, youtube_transcript_api, comtypes)
console:       False
icon:          icon.ico if present
```

### release.bat → release.ps1

```
release.bat:
  1. Check $env:GH_PAT is set, error if not
  2. Diagnostic block (NEW v1.20.1): echo CWD, script dir, full dist\ contents
  3. Verify dist\YTGrab.exe + dist\YTGrabUninstaller.exe exist
  4. powershell -File release.ps1

release.ps1:
  1. Read version from .release-please-manifest.json (the "." key)
  2. Verify both build artifacts exist (redundant with .bat check, but safe)
  3. GET /releases/tags/v<version> (find existing release for this tag)
  4. If exists:
       - With -Force: DELETE the release + its tag, recreate
       - Without -Force: attach assets to the existing release
  5. If not exists: POST /releases (create) with body pointing at YTGrab.exe
  6. Delete any existing assets with names matching what we're uploading
  7. Upload via uploads.github.com/repos/.../releases/<id>/assets:
        dist\YTGrab.exe              → asset
        dist\YTGrabUninstaller.exe   → asset
  8. Print success + URL
```

### release-please flow

```
On every push to main:
  1. release-please-action@v4 runs in GitHub Actions (Ubuntu)
  2. Reads release-please-config.json + .release-please-manifest.json
  3. Walks commits since the last released tag, classifies them:
       feat: → minor bump
       fix:  → patch bump
       feat!: / BREAKING CHANGE: → major bump
       chore: / ci: / test: / style: → no bump
  4. Opens or updates a single PR titled "chore(main): release X.Y.Z"
     containing:
       - Bumped .release-please-manifest.json
       - New CHANGELOG.md entry
       - Bumped <!-- x-release-please-version --> marker in index.html
  5. When the PR is merged:
       - Tags the merge commit vX.Y.Z
       - Publishes a GitHub Release with the changelog as the body
       - The release has NO binary assets at this point — release.bat
         uploads them in a separate manual step.
```

### Version propagation

```
Single source of truth: .release-please-manifest.json (`{".":  "1.20.0"}`)
       ↓
release-please bumps it on PR merge based on commits
       ↓
index.html's <!-- x-release-please-version --> marker auto-bumped
       ↓
build.bat → PyInstaller bundles .release-please-manifest.json into YTGrab.exe
       ↓
installer.py's _read_app_version() pulls from sys._MEIPASS at runtime
       ↓
Used by:
   - APP_VERSION constant (set at module load)
   - _register_uninstall_entry's DisplayVersion field
   - The version written to %INSTALL%\version.txt
   - The version compared against latest.tag_name in self-update
```

---

## 6. Known limitations

### Tested vs untested code paths

| Path | Status |
|---|---|
| NVENC encoder | **Tested ✓** by Sleepy on his NVIDIA box — produces 60 Mbps output for 4K60 source as designed |
| AMF encoder | **NOT tested** — no AMD GPU available. Code is written from ffmpeg docs + AMF docs; first AMD user is also the first tester |
| QSV encoder | **Tested partially** — Sleepy's machine has Intel iGPU and v1.19 (broken-NVENC-smoketest era) ran on QSV; the CBR re-tune in v1.20 hasn't been re-tested specifically on QSV |
| libx264 software fallback | **NOT tested** — hwaccel kicked in on every test machine. The auto-fallback chain has been exercised when NVENC was misconfigured but the libx264 result hasn't been verified |
| ffmpeg child cancel | **NOT tested** — the ✕ button + taskkill /F /T /PID path was added but Sleepy hasn't pressed cancel on a running encode |
| hwaccel runtime-disable gate (`_HWACCEL_DISABLED_AT_RUNTIME`) | **NOT tested** — would fire if a hwaccel codec passes smoketest but fails on real content; hasn't happened in any session |
| Auto-self-update (v1.18 → v1.19 → v1.20) | **NOT verified end-to-end** — Sleepy did fresh installs, not upgrades. The mechanism is plumbed but the on-machine upgrade path hasn't been confirmed working post-v1.19 |
| File / install size dynamics on a low-memory machine | **NOT tested** — all dev on a high-spec box |
| Smart App Control (Windows 11 strict mode) | **Acknowledged broken** — README documents the source-mode workaround |
| Apps & Features uninstall trigger | **Tested ✓ in v1.20.1 candidate** — the v1.20.0 ship had the `_unregister_arp_entry` patch in NEW YTGrab.exe but old YTGrabUninstaller.exe (download-from-GitHub bug); fixed in v1.20.1 by bundling. Once v1.20.1 ships and is uninstalled, the registry deletion will be verified |

### Single-OS

Windows-only. README + CONTRIBUTING explicitly scope out Mac/Linux. Source uses:
- Win32 directly (HWND, WM_NCLBUTTONDOWN, AppUserModelID, taskkill /FI)
- WebView2 backend
- WScript.Shell COM for shortcuts
- Explorer COM (Shell.Application) for window-reuse
- HKCU registry
- PowerShell as a generic scripting fallback

Porting would require rewriting all of these. Not in scope.

### Hardware assumptions

- 64-bit Windows 10 (build 17063+, ~Sept 2017) for `curl.exe` + `tar.exe` in fetch_ffmpeg.bat
- WebView2 runtime present (auto-installer on Win10; pre-installed on Win11)
- ~500 MB free disk for install + dependencies + sample downloads
- Internet access for: yt-dlp's actual download, optional auto-update check, optional yt-dlp's AV1 decoder downloads
- For hwaccel: a discrete GPU (NVIDIA/AMD) OR Intel iGPU with QSV support

### Edge cases not handled

- **No bandwidth tracking**: app downloads at full pipe; no throttle
- **No download queue prioritization**: parallel downloads each get their own thread, no fairness
- **No resume after crash**: if app dies mid-download, the partial file in the per-video folder is orphaned (not cleaned up; user has to manually delete)
- **No download history pagination**: if Downloads list grows past ~1000 entries, render perf degrades (no virtual scrolling)
- **No concurrency limit on /api/download** — user could fire 50 downloads at once and saturate disk + ffmpeg children
- **No yt-dlp version pinning** — `requirements.txt` says `yt-dlp>=2024.10.0`; if the user's venv was created on 2024-10 and YouTube changes its API, downloads break until they manually upgrade. `launch.bat` runs `pip install -U` only if the venv is being created fresh

### "Works on my machine" risks

- **NVENC params** are bedrock-compatible (legacy preset names, no Turing-only flags) but not exercised on Maxwell or Pascal
- **Apps & Features registration** uses HKCU but if a user has a corporate-locked HKCU (rare), registration silently fails (caught by try/except)
- **Long path handling**: title sanitization caps at 180 chars before colliding with Windows' 260-char MAX_PATH. Long video titles + deep INSTALL_DIR could still hit it
- **Anti-virus heuristics**: PyInstaller onefile binaries with embedded ffmpeg sometimes trigger false positives. Code-signing would help; we don't sign

---

## 7. Bugs / open issues / tech debt

### Currently broken or partially working

- **release.bat existence check** intermittently reports missing files even when both `dist\YTGrab.exe` and `dist\YTGrabUninstaller.exe` exist post-build. Cause unknown — diagnostic block added in v1.20.1 to dump CWD + dist listing on next reproduction. Until the diagnostic catches it on a real run, this is unresolved.
- **v1.20.0 shipped with broken APP_VERSION**: the registry's DisplayVersion read "1.19.1" while every other surface read "1.20.0" because APP_VERSION was a hardcoded constant that wasn't bumped when release-please bumped the manifest. v1.20.1 fixes by reading from the manifest; not yet shipped.
- **v1.20.0 `_unregister_arp_entry` is dead code on installed v1.20.0 machines**: those installs have the OLD v1.19 YTGrabUninstaller.exe (downloaded from GitHub at install time) which doesn't include the function. v1.20.1's bundling fix prevents this on future installs but doesn't help v1.20.0 users — they'll have leftover registry entries until they upgrade.

### Code that needs refactoring

- **server.py is 3,960 lines** in a single file. The download pipeline (~600 lines), the explorer-window logic (~400 lines), the title-bar / pywebview integration (~500 lines), the API endpoints (~800 lines), the bitrate/encoder/transcode helpers (~400 lines), and the heartbeat/shutdown plumbing (~200 lines) all coexist. A future refactor could split into `server/app.py`, `server/transcode.py`, `server/window.py`, `server/explorer.py` modules.
- **index.html is 5,859 lines** with HTML + CSS + JS in one file. By design (per CONTRIBUTING.md, the goal is "no build step for the frontend") but a tipping point is approaching where the grep-find-edit cycle gets brittle.
- **installer.py mixes concerns**: bootstrap detection, install GUI, update check, registry write, all in one file. ~789 lines is fine but the GUI + worker split is informal — a `bootstrap.py` for detection + dispatch and an `install_worker.py` for the actual work would clarify.
- **uninstaller.py PowerShell self-delete is a 50-line string-concatenated PS script**. Easier to maintain as a separate `.ps1` file bundled as data, but the current inline approach has the benefit of not needing an extra build artifact.
- **Two log files (`transcode-debug.log` and `ytgrab-uninst.log`)** with overlapping responsibilities. A single `app.log` rotated by day would be cleaner.

### Hardcoded values that should be config

- ffmpeg target preset (`medium` for hardware encoders, `veryfast` for libx264) — not user-tunable
- libx264 thread cap (`-threads 4`) — should scale with CPU count
- AAC bitrate (`192k`) — not user-tunable
- Audio padding subtraction in bitrate probe (`192` kbps) — assumes our output, fine for now
- Heartbeat poll interval (HEARTBEAT_POLL_SEC) and timeout (HEARTBEAT_TIMEOUT_SEC) — buried in server.py
- 8-second update-check timeout, 8-second smoketest timeout, 30-second download timeout — sprinkled

### Missing error handling

- **yt-dlp version mismatch with YouTube**: if YT changes their API and yt-dlp hasn't been updated, downloads fail with cryptic errors. We don't surface "you should upgrade yt-dlp" in the UI; the user sees the raw exception.
- **Disk-full mid-transcode**: ffmpeg's exit code is captured but we don't distinguish "disk full" from "bad params"; user just sees a generic error
- **Network down mid-download**: yt-dlp retries internally but eventual exhaustion produces a bare exception in the job's error field, not a "check your connection" message
- **WebView2 runtime missing**: pywebview falls back to chrome-app-mode (which is at server.py:3216) but the fallback is shallow — most window-management features (drag, min/max, custom title bar) don't work
- **Settings save/load races**: localStorage writes happen on every change; if WebView2 dies mid-write, the next launch reads truncated JSON and resets to defaults silently

### Logging gaps

- **Successful downloads aren't logged** — only errors. Hard to retroactively answer "did that download finish?"
- **App startup events aren't logged** — no record of "started at HH:MM:SS, version X, ffmpeg detected as Y"
- **No request log** for `/api/*` endpoints — hard to debug "why didn't the UI update"
- **Windows-side stdout** is captured by PyInstaller's runw.exe and discarded silently. `print()` calls in the codebase land in the void unless we're running source mode.

---

## 8. Ship status — v1.20.0 / v1.20.1

### v1.20.0 — what was supposed to ship

- Encoder pipeline rewrite: NVENC/AMF/QSV detection, smoketest at 256x256, CBR-with-minrate, hwaccel runtime-disable gate, libx264 auto-fallback
- Bitrate probe chain with audio-share subtraction
- Resolution+fps-aware floor table
- yt-dlp `format_sort` override (VP9 over AV1)
- Apps & Features registration on install
- Uninstaller cleans the registry key + yt-dlp cache + `[arp]` diagnostic logging
- Recursive registry subkey deletion
- Color picker: Liquid Glass redesign (distilled-water + injected-color), hex input box removed, picker reactive to picked color (not just hue family)
- Per-row "Convert to AE-friendly" right-click action
- File inventory + uninstaller audit doc

### v1.20.0 — what actually shipped (with bugs)

- All of the above ✓
- **Bug 1: APP_VERSION desync** — registry's DisplayVersion shows 1.19.1 because the constant in installer.py wasn't bumped along with the manifest
- **Bug 2: `_unregister_arp_entry` doesn't run** for users who installed v1.20.0 — their installed YTGrabUninstaller.exe was downloaded from GitHub at install time and is the v1.19 binary, which doesn't have the function
- **Bug 3: release.bat sometimes reports missing files** even after build.bat succeeds — cause unknown, diagnostic block added in v1.20.1

### v1.20.1 — what's in flight

- Fix Bug 1: `APP_VERSION = _read_app_version()` reads from `.release-please-manifest.json` bundled in the binary. Future release-please bumps propagate automatically with no manual intervention.
- Fix Bug 2: `YTGrab.spec` now bundles `dist\YTGrabUninstaller.exe`. Build order in `build.bat` swapped (Uninstaller first). `installer.py`'s `_extract_bundled_uninstaller()` replaces `_download_uninstaller_from_github()`. v1.20.1 installs ship with a same-version uninstaller, no network round-trip.
- Diagnose Bug 3: `release.bat` now echoes CWD + script dir + full dist listing before the existence check. On the next reproduction, the log will identify the runtime cause (CWD mismatch / file missing at check time / something else).

### v1.20.1 ship status (right now)

- **Code committed**: NO — changes are in the working tree, not yet committed
- **Pushed to main**: NO
- **release-please PR**: not opened (no push has happened)
- **Local rebuild done**: NO
- **Tested**: NO

The v1.20.1 changes are written and lint-clean but Sleepy explicitly said no shipping until things work. So nothing has moved beyond "uncommitted edits in the working tree."

---

## 9. Roadmap (discussed, not built)

### Universal encoder coverage

- Test the AMF code path on a real AMD machine (Radeon RX or recent APU). The CBR config was written from docs but no AMD user has run it.
- Test the QSV CBR path on Intel iGPU. v1.19 ran on QSV in VBR mode (which undershot bitrate); v1.20.0 changed to CBR but the change hasn't been re-validated on an Intel-only machine.
- Test the libx264 software fallback by force-disabling hwaccel and downloading. Currently never actually runs in production because every test machine had a working hwaccel codec.

### Quality mode toggle

User asked for a UI toggle: "Standard mode" (efficient VBR, smaller files, content-driven bitrate) vs "Max Quality mode" (forced CBR + bitrate floor, predictable size, AE-clean). v1.20 ships with Max Quality always-on (CBR-with-minrate). The toggle would let users opt out for casual downloads where they don't care about editing.

### libx264 advanced fallback options

Currently libx264 fallback uses `preset veryfast` to keep low-end machines from overheating. A future "I have time, give me quality" option could let the user pick `slow` or `veryslow` for users with capable hardware who don't mind a long encode.

### File-size-aware mode for casual users

User mentioned a desire to differentiate "I want this for AE" (current behavior — 4K60 transcodes to 60 Mbps regardless of source) from "I just want to watch this on my phone" (would prefer a smaller file). Same source video could land at 4 GB (max quality) or 400 MB (web-friendly) depending on intent. Not built.

### In-app YouTube search + embedded player

User mentioned wanting to search YouTube and watch in-app before deciding to download. Would use yt-dlp's `--default-search ytsearch:` mode. Embedded player would be an HTML5 `<video>` tag with custom controls. "Playlist Mk" was cited as visual reference but no screenshots have been shared.

### Multi-page UI

User mentioned wanting page-based navigation: Welcome → Main hub → Search / Downloads / Previously Deleted / Settings. Active queue would move to a top-right collapsible widget. Not built; would be a substantial rewrite of index.html.

### Recent search history

A clearable list of recent searches/URLs. Not built.

### Bare-minimum trim/cut editing

ffmpeg-based trim a clip from a downloaded video. Not built.

### Read APP_VERSION at PyInstaller spec-evaluation time

Currently APP_VERSION is read at runtime from the bundled manifest. An alternate pattern: have YTGrab.spec read the manifest at build time and inject the version into a generated `_version.py` constant module. Pros: zero-runtime cost, doesn't require manifest in the bundle. Cons: more build complexity. Either approach works; we picked runtime read in v1.20.1 because it's smaller.

---

## 10. The original "AE rejects files as damaged" problem

### Origin

Sleepy's friend tried to import a YT Grab download into After Effects and got:

```
After Effects error: The source compression type is not supported.
After Effects error: file 'Madara vs Shinobi Alliance Full Fight [HD].mp4'
   cannot be imported -- this '.mp4' file is damaged or unsupported.
```

The file wasn't actually damaged. yt-dlp at >=1440p hands back VP9 or AV1 video and Opus audio, packed inside an mp4 container. AE's native importer can't decode VP9 / AV1 / Opus regardless of the container extension. Other downloaders that work with AE either cap output at 1080p (where YouTube serves H.264) or transcode behind the scenes.

### The fix (current state)

```
1. Every video download now triggers _transcode_to_ae_friendly() in
   server.py after yt-dlp finishes.
2. Encoder priority chain: NVENC → AMF → QSV → libx264 (with smoketest
   gating, runtime-disable on real-content failure, and software
   fallback by recursion).
3. Output: H.264 video + AAC audio in mp4 container. AE/Premiere/DaVinci
   all import natively.
4. Bitrate target: max(probed source video bitrate, resolution+fps floor).
   So a 1.8 Mbps source 4K60 video transcodes at 60 Mbps target — gives
   the H.264 encoder enough headroom that re-encoding doesn't compound
   the source's existing compression.
5. Mode: CBR-with-minrate. Output literally hits the target bitrate
   (per-codec-specific config, see section 3).
6. Per-row "Convert to AE-friendly" right-click action lets users repair
   videos downloaded before v1.19 (or on a machine without ffmpeg) by
   re-running the same transcode.
7. Active queue shows live "transcoding · NN%" status during the encode
   with cancel button + cascade-kill on shutdown.
```

### What's been verified

- Encoder selection works (NVENC selected on Sleepy's box, log confirms)
- Bitrate target match works (60 Mbps target → 60,237 kbps output for 4K60 source — within 0.4%)
- Source bitrate probe works (correctly reads 4 Mbps source on a low-quality YouTube video)
- yt-dlp format_sort override picks VP9 4 Mbps over AV1 1.8 Mbps (verified via `yt-dlp -F` output)
- Output mp4 plays in standard players (VLC, Windows Media Player, Chrome HTML5 video)
- File metadata is clean ("Year: 65124" garbage from `-map_metadata 0` removed by switching to `-map_metadata -1`)

### What's NOT been verified

**The actual AE import.** Sleepy doesn't have After Effects installed right now. The transcode produces a file that is, by spec, AE-friendly — H.264 baseline-or-main profile in mp4 container with AAC audio at standard sample rates — but no one has dragged the output mp4 into AE and confirmed it imports without error.

### Verification when AE is back

```
1. Download any 4K video via YT Grab v1.20+ (auto-transcode active)
2. Locate the output: %LOCALAPPDATA%\Programs\YTGrab\downloads\<title>\<title>.mp4
3. Open After Effects → File → Import → File → select the .mp4
4. Expected: import succeeds without error dialog, clip appears in the
   Project panel with correct duration / resolution / framerate
5. Drag clip onto a new composition. Scrub the timeline.
6. Expected: video plays back smoothly, no black frames, no codec error

If AE imports cleanly: the AE-rejects-files problem is solved end-to-end.
If AE still rejects: paste the exact AE error dialog. The fix would
likely be one of:
   - Switch profile from main to baseline (lower compatibility ceiling
     but broader support)
   - Drop -movflags +faststart if AE doesn't like front-loaded moov atoms
   - Force -level 4.1 or 4.2 for older AE versions that cap at H.264 L4.0
```

---

## What to do next (decision points for Sleepy)

This doc is descriptive, not prescriptive. The actual decision-makers below are yours — pick one or many or none:

1. **Ship v1.20.1 as-is** to fix the v1.20.0 desync + dead-code uninstaller bugs, before Sleepy's friends notice the registry-version-mismatch in Apps & Features.
2. **Wait on v1.20.1** until the release.bat diagnostic catches the runtime cause of Bug 3, fix that, then ship.
3. **Verify AE end-to-end** when AE is reinstalled, document the result here, ship a "v1.20.1 ships with verified AE import" release note.
4. **Tackle a roadmap item** (universal encoder testing, quality toggle, search/player) as a v1.21.x.
5. **Refactor server.py / index.html** before they get bigger. Painful now but more painful later.
6. **Drop the project for a while** — everything works for the personal-use case. Friends can use what's on GitHub.

None of those are mutually exclusive.
