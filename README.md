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
