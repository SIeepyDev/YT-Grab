# YT Grab

A clean, local-first YouTube downloader for Windows. Native desktop window, premium dark UI, no tracking, no ads, no uploads. Your videos stay on your machine.

![status: beta](https://img.shields.io/badge/status-beta-orange) ![windows only](https://img.shields.io/badge/windows-only-blue) ![license: MIT](https://img.shields.io/badge/license-MIT-green)

## Features

- **Three-column workspace** — History (files on disk) on the left, paste + format picker in the middle with your Previous Downloads log beneath, and the Active queue on the right that fills the moment you hit download.
- **Every format** — MP4 / MKV / WEBM up to 4K (2160p VP9), plus MP3 / M4A / OPUS / WAV / FLAC with bitrate selection for lossy audio.
- **Embedded metadata + thumbnail** — downloads ship with cover art baked into the file.
- **Transcript + thumbnail sidecars** — optional `.txt` and `.jpg` alongside the video, one click to open / view / delete.
- **Rename from the UI** — F2, double-click the title, or the pencil button. Renames the folder and every file inside on disk.
- **Previous Downloads log** — once you delete something, it stays in the log so you can redownload with one click.
- **Ctrl+Z** — undoes the last delete (files come back from the Recycle Bin reference).
- **Settings panel** — accent color, default format / quality, default sidecar toggles. Persists across sessions.
- **Native Windows integration** — pinned Start Menu + Desktop shortcuts, own taskbar identity, WebView2 under the hood (no Chrome required).

## Quick start (source mode, dev-friendly)

Requires **Python 3.10+** on Windows.

```powershell
git clone https://github.com/SIeepyDev/yt-grab.git
cd yt-grab
.\launch.vbs
```

First launch creates a venv and installs requirements. Subsequent launches are silent and instant. Double-click `launch.vbs` from Explorer or pin the auto-created Start Menu shortcut.

For full metadata + thumbnail embedding, run `fetch_ffmpeg.bat` once — it downloads ffmpeg + ffprobe (~100 MB) into `bin/`. Without them, downloads still work; thumbnail-embed and transcode postprocessors skip gracefully.

## Build a standalone .exe

```powershell
.\build.bat
```

Produces `dist\YTGrab.exe` — a single-file Windows binary with Python, every dependency, and ffmpeg bundled in. No install, no dependencies for the end user. About 60 MB.

```powershell
.\package.bat
```

Wraps the exe plus a source fallback into `dist\YTGrab.zip` (ships with a `FRIEND_README.txt` that explains both modes). Send this zip to anyone with a recent Windows machine — they unzip and run.

### Smart App Control note

Windows 11's Smart App Control (SAC) blocks unsigned PyInstaller binaries on some machines with no "Run anyway" option. The packaged zip includes a `source/` fallback — run `source\launch.vbs` and it runs from Python just fine. SAC leaves Python scripts alone.

## What lives where

| Path | What |
|---|---|
| `server.py` | Flask backend + pywebview native window. Download pipeline, history, Explorer integration, Windows shortcut + taskbar branding. |
| `index.html` | Entire UI — HTML + CSS + JS in one file for simplicity. Dark theme with accent color CSS vars driven by the settings panel. |
| `YTGrab.spec` | PyInstaller spec. `console=False`, bundles icon + index.html + ffmpeg. |
| `launch.bat` | Verbose launcher (creates venv, installs reqs, kills zombie ports). |
| `launch.vbs` | Silent launcher. Default entry point for users. |
| `fetch_ffmpeg.bat` | One-time fetch of the full ffmpeg build (for thumbnail/metadata embed). |
| `build.bat` + `build_icon.py` | Build pipeline for the .exe. |
| `package.bat` | Wraps exe + source fallback into a shippable zip. |
| `clean.bat` | Factory reset — wipes venv, dist, bin, downloads, history. |
| `downloads/` | User-facing output. Per-video folders with the main file + optional sidecars. |
| `history.json` | Current on-disk downloads. |
| `activity.json` | Log of everything ever downloaded (including deleted). |
| `settings.json` | User preferences from the settings panel. |

## Architecture

- **Flask** serves `/api/*` JSON endpoints on `localhost:8765`.
- **pywebview + WebView2** wraps the page as a native desktop window (no Chrome dependency, own taskbar identity).
- **yt-dlp** handles the actual extraction and format negotiation with YouTube.
- **ffmpeg + ffprobe** (in `bin/`) handle transcoding, thumbnail conversion, metadata embed.
- **send2trash** makes delete reversible — files go to the Recycle Bin.
- **comtypes** + Shell.Application COM powers the "reuse existing Explorer window" behavior when you hit Open.

The Flask server runs in a background thread; the main thread is pywebview. A heartbeat loop on the frontend + `navigator.sendBeacon('/api/shutdown')` on tab close means closing the window exits the process — no zombies.

## Privacy

- Zero telemetry.
- No remote API calls other than YouTube itself (via yt-dlp).
- Nothing leaves your machine except the video fetch from YouTube's CDN.
- History and activity logs are local JSON files you can delete or inspect.

## Known limitations

- **Windows only.** The pywebview + WebView2 + Win32 taskbar integration is Windows-specific. Cross-platform support is not a near-term goal.
- **Smart App Control can block the signed-less .exe** — fall back to source mode.
- **yt-dlp moves fast.** If YouTube ships a breaking extractor change, run `launch.bat` — it auto-upgrades yt-dlp on each start.

## License

[MIT](LICENSE). Use it, fork it, ship it, steal ideas. Attribution appreciated but not required.

## Author

Built by [SleepyDev](https://github.com/SIeepyDev). Part of the Luna workspace tools family.

## Changelog

### v1.2 — Theming Polish (2026-04)

Themebar grew up. The right-side Themes panel is no longer a gear with a color picker — it's a proper personalization surface with four sections: Theme mode, Layout, Background, and Gradient.

**Theme mode**
- Three-card picker: **Light**, **Dark**, **System**. System listens to `prefers-color-scheme` and follows your OS live — flip Windows' theme and the app flips with it, no restart.
- Switching modes now *transitions* instead of snapping. 380 ms cubic-bezier cross-fade on background, text, borders, and accent surfaces. Respects `prefers-reduced-motion`.

**Layout section**
- Three layout presets: **Balanced** (default three-column), **Focus** (middle column wider, sides slimmer for when you're paste-and-going), **Stacked** (single-column flow for narrow windows or screenshots).
- Per-column visibility toggles for History / Middle / Queue. Hide what you don't use.

**Background section**
- Seven base backgrounds across both modes: Void, Graphite, Midnight, Slate for dark; Cream, Paper, Snow for light.
- Optional subtle noise overlay — adds film-grain texture without affecting contrast.

**Gradient section**
- Six gradient presets: Aurora, Sunset, Ocean, Plum, Ember, plus **Accent-derived** (auto-generates a mesh from your current accent color).
- Gradient intensity slider (0–100 %) and optional slow animation that orbits the hue field over ~40 s.

**Under the hood**
- Accent palette now derives from a single hex via `color-mix(in oklch, …)` — `--accent`, `--accent-hover`, `--accent-subtle`, `--accent-text`, and `--accent-contrast` stay perceptually consistent across the full color range. No more muddy-looking hovers on amber or lime.
- Auto-contrast text: buttons and badges on accent backgrounds pick black or white automatically based on Rec. 709 luminance. Pastel accents flip to dark text, dark accents stay white-on-accent.
- Every themebar control is a single `data-*` attribute on `<html>` — CSS does the work, JS just toggles an attribute. Fast and declarative.
- New settings persisted across sessions: `layoutPreset`, `colsVisible`, `bgPreset`, `bgNoise`, `gradientPreset`, `gradientIntensity`, `gradientAnimate`.

**Also in this release**
- Color picker inner disc is now true Liquid Glass — transparent, fitted within the hue ring, frosted with `backdrop-filter: blur(18px) saturate(1.4)`. Feels like Apple's material, not a solid JS-painted gradient.

### v1.1 — Batch, subtitles, context menu (2026-04)

- **Playlist support** — paste a playlist URL, get a picker modal with thumbnails and checkboxes. Select all / none / individual, queues via the batch pipeline.
- **Subtitles (.srt)** — new "Save subtitles" checkbox. Requests both human-authored and auto-generated English captions. New Subs combo-button on history rows.
- **Right-click context menu on history rows** — Copy URL, Open on YouTube, Reveal in folder, Rename, Redownload, Delete.
- **Clipboard auto-detect** — copy a YouTube URL anywhere, return to YT Grab, URL auto-fills with a "Enter to load" hint.
- **Multi-URL batch paste** — paste newline-separated YouTube URLs, they all queue.
- **Keyboard shortcuts** — press `?` for cheatsheet, `Ctrl+,` opens settings.
- Page scroll locked, only cards scroll internally. 14-color accent palette (was 6). Dark Windows title bar via DWM immersive. Flicker-free launch.
- New `/api/playlist_info` endpoint uses yt-dlp `flat_playlist` for ~1 s listing of 50 videos (vs ~30 s with per-video probe).

### v1.0 — Initial prototype

Three-column workspace, every format up to 4K, embedded metadata + thumbnail, transcript + thumbnail sidecars, rename-on-disk, Previous Downloads log, Ctrl+Z undo, native Windows integration.
