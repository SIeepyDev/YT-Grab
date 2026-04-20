"""YT Grab -- Flask backend.

Single-process, single-machine. Designed to be launched from launch.bat,
listen on localhost:8765, and drive a single-page UI in the user's browser.

Architecture:
  * GET  /                  -> serves index.html
  * POST /api/info          -> probe URL with yt-dlp, return metadata
  * POST /api/download      -> kick off a download job in a background thread,
                               return a job_id immediately
  * GET  /api/progress/<id> -> poll current progress for a job
  * POST /api/transcript    -> fetch captions as plaintext
  * GET  /api/history       -> list recent completed downloads
  * GET  /api/open_folder   -> open the downloads/ folder in Explorer

State:
  * jobs                   - in-memory dict keyed by job_id, progress/status/path
  * history.json           - persisted list of recent completed downloads
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, Response

# ---------------------------------------------------------------------------
# Paths + setup
# ---------------------------------------------------------------------------
# PyInstaller onefile bundles extract to a temp dir at runtime, with
# sys._MEIPASS pointing at it. When frozen, we want:
#   * RESOURCE_DIR (for index.html) to be the temp extraction dir
#   * BASE_DIR (for downloads/, history.json, logs) to be NEXT TO the .exe
#     so the user can see their files and the state persists across runs
# When running from source, both are just the script's own directory.
_IS_FROZEN = getattr(sys, "frozen", False)
if _IS_FROZEN:
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))).resolve()
    BASE_DIR = Path(sys.executable).parent.resolve()
else:
    RESOURCE_DIR = Path(__file__).parent.resolve()
    BASE_DIR = Path(__file__).parent.resolve()

DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)
# Dedicated "soft-delete archive" for items the user removed from
# History. Two-stage delete: History X moves folder here; Previous X
# (or Clear Previous) sends from here to the Recycle Bin permanently.
# Undo restores from here back to downloads/.
PREVIOUS_DIR = BASE_DIR / "previous_downloads"
PREVIOUS_DIR.mkdir(exist_ok=True)
HISTORY_FILE = BASE_DIR / "history.json"
ACTIVITY_FILE = BASE_DIR / "activity.json"
PORT = 8765

# ---------------------------------------------------------------------------
# Delete-undo stack. When the user clicks X on a history row or Clear All,
# we (a) send the actual media files to the Windows Recycle Bin so they're
# recoverable through standard OS UI, and (b) push the deleted entries
# onto this stack so Ctrl+Z in the frontend can restore the HISTORY row.
# Stack is in-memory only (reset on server restart) and capped so a long
# session doesn't balloon memory. Each entry is a list of history dicts
# because Clear All is a single batch operation.
UNDO_STACK_MAX = 10
_undo_stack = []
_undo_lock = threading.Lock()


def _trash_file(path):
    """Send a file to the OS Recycle Bin. No-ops cleanly if the file is
    missing (already deleted, moved, never saved). Returns True if we
    actually sent something to the bin."""
    if not path:
        return False
    p = Path(path)
    if not p.exists():
        return False
    try:
        import send2trash
        send2trash.send2trash(str(p))
        return True
    except Exception as e:
        # Fall back to os.remove if send2trash isn't available (shouldn't
        # happen -- it's in requirements -- but belt + braces). At least
        # we try to honor the "delete the file" contract even without the
        # Recycle Bin safety net.
        try:
            os.remove(str(p))
            return True
        except Exception:
            print(f"[yt-dl] failed to trash {path}: {e}", file=sys.stderr)
            return False


def _trash_all_sidecars(entry):
    """Send an entry's folder to the Recycle Bin. This is the
    PERMANENT delete -- used when the user removes an item from
    Previous Downloads, or Clears all of Previous. For soft delete
    from History, use _soft_delete_to_previous instead.

    Newer entries have a 'folder' field pointing at the per-video
    subfolder -- for those we trash the whole folder as one unit.
    Older entries fall back to trashing individual files."""
    folder = entry.get("folder")
    if folder:
        folder_path = Path(folder)
        if folder_path.exists():
            _trash_file(folder)
            return
    for key in ("filename", "transcript_path", "thumbnail_path", "subtitle_path"):
        path = entry.get(key)
        if path:
            _trash_file(path)


def _soft_delete_to_previous(entry):
    """Soft delete: move the entry's per-video folder from downloads/
    to previous_downloads/. The file stays recoverable without touching
    the Recycle Bin. Entry dict is mutated in-place with updated paths
    so that later operations (undo, permanent delete, open folder)
    find the new location.

    Returns True if we actually moved something, False on legacy/missing
    entries where the caller should treat it as already-gone."""
    folder = entry.get("folder")
    if not folder:
        # Legacy flat-layout entry with no folder -- fall back to
        # Recycle Bin since there's no per-item folder to move.
        for key in ("filename", "transcript_path", "thumbnail_path", "subtitle_path"):
            p = entry.get(key)
            if p: _trash_file(p)
        return False

    src = Path(folder)
    if not src.exists():
        # Already gone. Nothing to do.
        return False

    # Collision-safe destination: if a folder with this name already
    # exists in previous_downloads/ (user deleted + re-downloaded +
    # deleted again), suffix with a number so we don't clobber.
    dst = PREVIOUS_DIR / src.name
    counter = 2
    while dst.exists():
        dst = PREVIOUS_DIR / f"{src.name} ({counter})"
        counter += 1

    try:
        src.rename(dst)
    except Exception as e:
        # Cross-drive or permission issue -- fall back to Recycle Bin
        # so the "delete" contract still holds.
        print(f"[yt-grab] soft-delete rename failed ({e}); using Recycle Bin",
              file=sys.stderr)
        _trash_file(folder)
        return True

    # Rewrite path fields to point at the new location so subsequent
    # ops (hard delete, undo, open folder) find the moved folder.
    new_folder = str(dst.resolve())
    entry["folder"] = new_folder
    entry["in_previous"] = True   # marker used by restore/hard-delete
    for key in ("filename", "transcript_path", "thumbnail_path", "subtitle_path"):
        p = entry.get(key)
        if p:
            entry[key] = p.replace(str(src), new_folder)
    return True


def _restore_from_previous(entry):
    """Undo: move a soft-deleted folder from previous_downloads/ back
    to downloads/. Called by the undo stack. Updates entry paths
    back to the downloads/ location. Returns True on success."""
    folder = entry.get("folder")
    if not folder or not entry.get("in_previous"):
        return False
    src = Path(folder)
    if not src.exists():
        return False
    dst = DOWNLOADS_DIR / src.name
    # If the user re-downloaded the same video while this one sat in
    # previous, there could be a name collision. Suffix to stay safe.
    counter = 2
    while dst.exists():
        dst = DOWNLOADS_DIR / f"{src.name} ({counter})"
        counter += 1
    try:
        src.rename(dst)
    except Exception as e:
        print(f"[yt-grab] restore rename failed: {e}", file=sys.stderr)
        return False
    new_folder = str(dst.resolve())
    entry["folder"] = new_folder
    entry["in_previous"] = False
    for key in ("filename", "transcript_path", "thumbnail_path", "subtitle_path"):
        p = entry.get(key)
        if p:
            entry[key] = p.replace(str(src), new_folder)
    return True


# ---------------------------------------------------------------------------
# Heartbeat / shutdown state -- used by the "close the tab => app exits"
# feature. The frontend pings /api/heartbeat every 20s. If we stop hearing
# from it (either via explicit /api/shutdown from a beforeunload beacon,
# OR because HEARTBEAT_TIMEOUT_SEC has passed with no ping), we exit --
# UNLESS there's an active download, in which case we stick around silently
# to finish it before shutting down. This matches the Luna mental model
# where closing the window closes the app.
# ---------------------------------------------------------------------------
HEARTBEAT_TIMEOUT_SEC = 90   # grace period after last ping before we exit
HEARTBEAT_POLL_SEC = 5       # how often the monitor thread wakes up
_last_heartbeat = time.time()
_heartbeat_lock = threading.Lock()
_pending_shutdown = False


def _brand_console():
    """Windows-only: replace the console window's title and icon so the
    app looks like YT Grab in the taskbar, not `python.exe`. Does
    nothing on non-Windows or if any of the Win32 calls fail -- the app
    works either way, this is pure cosmetics."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        # 1. Window title shown in the taskbar + top of the console window
        ctypes.windll.kernel32.SetConsoleTitleW("YT Grab")

        # 2. Icon on the taskbar + the console window's top-left corner.
        # Load icon.ico from wherever the app resources live, fall back to
        # the exe's directory when frozen. LoadImageW flags:
        #   IMAGE_ICON = 1, LR_LOADFROMFILE = 0x0010, LR_DEFAULTSIZE = 0x0040
        icon_candidates = [RESOURCE_DIR / "icon.ico", BASE_DIR / "icon.ico"]
        icon_path = next((p for p in icon_candidates if p.exists()), None)
        if not icon_path:
            return
        hicon = ctypes.windll.user32.LoadImageW(
            None, str(icon_path), 1, 0, 0, 0x00000010 | 0x00000040
        )
        if not hicon:
            return
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            # WM_SETICON: 0x0080, wParam 0 = small icon, 1 = large icon
            ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, hicon)
            ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, hicon)
    except Exception:
        pass  # cosmetic only; never block startup on this


def _which(exe_name):
    """Return full path to exe_name on PATH, or None."""
    import shutil
    p = shutil.which(exe_name)
    return p if p else None


def _find_ffmpeg_and_ffprobe():
    """Find ffmpeg AND ffprobe. yt-dlp's postprocessors (merge, thumbnail
    embed, metadata embed) need BOTH binaries to work. Lookup order:
      1. A bundled bin/ folder next to the app (built by fetch_ffmpeg.bat
         or included in the PyInstaller datas). This is preferred because
         it's self-contained and known-good.
      2. imageio-ffmpeg's bundled ffmpeg (pip-installed binary; only
         ffmpeg, no ffprobe -- so we ONLY use this for the ffmpeg slot
         and keep searching for ffprobe).
      3. System PATH (user has a global ffmpeg install, e.g. via winget).

    Returns (ffmpeg_dir, has_ffprobe) where:
      * ffmpeg_dir is a directory path to pass to yt-dlp's ffmpeg_location
        (yt-dlp looks in that directory for both binaries). None if we
        couldn't find even ffmpeg, in which case yt-dlp will use PATH.
      * has_ffprobe is True if ffprobe is usable somewhere. When False,
        we skip postprocessors that need it (EmbedThumbnail, FFmpegMetadata)
        so downloads don't fail at 99%.
    """
    # 1. Local bin/ folder (set up by fetch_ffmpeg.bat or bundled)
    for search_dir in (BASE_DIR / "bin", RESOURCE_DIR / "bin"):
        ff = search_dir / "ffmpeg.exe"
        fp = search_dir / "ffprobe.exe"
        if ff.exists() and fp.exists():
            return (str(search_dir), True)

    # 2. imageio-ffmpeg bundled ffmpeg (NO ffprobe in this package --
    # we still need to find ffprobe separately)
    ff_path = None
    try:
        import imageio_ffmpeg
        candidate = imageio_ffmpeg.get_ffmpeg_exe()
        if candidate and os.path.exists(candidate):
            ff_path = candidate
    except Exception:
        pass

    # 3. PATH -- check for both binaries globally
    path_ffprobe = _which("ffprobe")
    path_ffmpeg = _which("ffmpeg")

    if path_ffmpeg and path_ffprobe:
        # User has both on PATH (e.g. `winget install Gyan.FFmpeg`)
        return (str(Path(path_ffmpeg).parent), True)

    if ff_path and path_ffprobe:
        # ffmpeg from imageio_ffmpeg, ffprobe from PATH. Tell yt-dlp
        # the directory of path_ffprobe -- but that directory also has
        # to contain ffmpeg or yt-dlp looks for it separately. Easiest:
        # point at the ffprobe directory, and the ffmpeg on PATH is also
        # in a PATH-searchable spot. Let yt-dlp sort it out.
        return (str(Path(path_ffprobe).parent), True)

    if ff_path:
        # ffmpeg available (imageio-ffmpeg), ffprobe missing entirely.
        # Return the ffmpeg dir so merging works, flag ffprobe as absent
        # so we skip postprocessors that need it.
        return (str(Path(ff_path).parent), False)

    if path_ffmpeg:
        return (str(Path(path_ffmpeg).parent), bool(path_ffprobe))

    return (None, False)


FFMPEG_LOCATION, HAS_FFPROBE = _find_ffmpeg_and_ffprobe()

app = Flask(__name__, static_folder=str(RESOURCE_DIR), static_url_path="")

# ---------------------------------------------------------------------------
# In-memory job registry
# ---------------------------------------------------------------------------
# jobs[job_id] = {
#     "status": "pending" | "downloading" | "done" | "error",
#     "percent": 0-100,
#     "speed": str,         # human-readable ("1.2MiB/s")
#     "eta": str,           # human-readable ("00:23")
#     "filename": str|None, # final file path (absolute)
#     "title": str,
#     "error": str|None,
#     "started_at": iso timestamp,
# }
jobs = {}
jobs_lock = threading.Lock()


def _update_job(job_id, **fields):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(fields)


def _new_job(title):
    job_id = uuid.uuid4().hex[:12]
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "pending",
            "percent": 0,
            "speed": "",
            "eta": "",
            "filename": None,
            "title": title,
            "error": None,
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
    return job_id


# ---------------------------------------------------------------------------
# History persistence
# ---------------------------------------------------------------------------
def _load_history():
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_history(entries):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(entries[-50:], f, indent=2)
    except Exception as e:
        print(f"[yt-dl] WARN: could not persist history: {e}", file=sys.stderr)


def _add_to_history(entry):
    hist = _load_history()
    hist.append(entry)
    _save_history(hist)


# ---------------------------------------------------------------------------
# Activity log -- "Previous Downloads". Entries that LEAVE history (via X
# or Clear All) land here with a deleted_at stamp. This gives the user a
# second-tier log of everything they've ever downloaded, like a browser's
# download history, separate from the "files you currently have on disk"
# view. Files in activity are no longer on disk (they were trashed when
# the history entry was removed), so activity entries don't get an Open
# button -- just Redownload + remove-from-list.
# ---------------------------------------------------------------------------
def _load_activity():
    if not ACTIVITY_FILE.exists():
        return []
    try:
        with open(ACTIVITY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_activity(entries):
    try:
        # Keep last 500 activity entries. Plenty of headroom; caps unbounded
        # growth if the user does a lot of download-then-delete cycles.
        with open(ACTIVITY_FILE, "w", encoding="utf-8") as f:
            json.dump(entries[-500:], f, indent=2)
    except Exception as e:
        print(f"[yt-dl] WARN: could not persist activity: {e}", file=sys.stderr)


def _log_activity(entries):
    """Append one or more history entries to the activity log with a
    deleted_at timestamp. Accepts a single dict or a list of dicts."""
    if isinstance(entries, dict):
        entries = [entries]
    if not entries:
        return
    activity = _load_activity()
    now = datetime.now().isoformat(timespec="seconds")
    for e in entries:
        row = dict(e)
        row["deleted_at"] = now
        activity.append(row)
    _save_activity(activity)


# ---------------------------------------------------------------------------
# yt-dlp wrappers
# ---------------------------------------------------------------------------
def _yt_opts_base():
    """Shared yt-dlp options. Keeping these in one place means every
    download obeys the same filename template and progress hooks."""
    opts = {
        "outtmpl": str(DOWNLOADS_DIR / "%(title)s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": False,
    }
    # Bundled ffmpeg path (PyInstaller builds + dev mode with imageio-ffmpeg
    # installed). Falls back to yt-dlp's own PATH search if we didn't find one.
    if FFMPEG_LOCATION:
        opts["ffmpeg_location"] = FFMPEG_LOCATION
    return opts


VIDEO_FORMATS = {"mp4", "mkv", "webm"}
AUDIO_FORMATS = {"mp3", "m4a", "opus", "wav", "flac"}
LOSSLESS_AUDIO = {"wav", "flac"}  # audio_bitrate ignored for these


def _fmt_selector(container, resolution):
    """Pick a yt-dlp format selector string.

    "best" resolution = no height cap at all; grab the absolute
    highest-resolution video YouTube offers (usually VP9/AV1 at 2160p
    or 4320p for some channels) + best audio. This is the default now
    because "premium feel" = max quality unless the user explicitly
    asks for less.

    Codec filters like [ext=mp4] are DROPPED for numeric heights above
    1080p -- YouTube doesn't serve 2160p/1440p in H.264 so filtering by
    ext=mp4 silently falls back to 1080p. By letting yt-dlp pick any
    codec at the target height (VP9/AV1) and letting ffmpeg merge into
    the requested container, we actually get 4K when 4K is available.
    """
    height_map = {"2160": 2160, "1440": 1440, "1080": 1080, "720": 720, "480": 480, "360": 360}
    res = str(resolution or "best").lower()

    if res == "best":
        # No height cap. "bestvideo+bestaudio" gets top-quality streams;
        # fallback to "best" if the site only offers pre-merged formats.
        return "bestvideo+bestaudio/best"

    h = height_map.get(res, 1080)

    # Only apply codec filter for <=1080 where H.264 mp4 is actually
    # available. Above 1080, drop the filter so 1440p/2160p VP9 gets
    # picked up instead of falling back to 1080p.
    if h <= 1080 and container == "mp4":
        return (
            f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={h}]+bestaudio/"
            f"best[height<={h}]/best"
        )
    if h <= 1080 and container == "webm":
        return (
            f"bestvideo[height<={h}][ext=webm]+bestaudio[ext=webm]/"
            f"bestvideo[height<={h}]+bestaudio/"
            f"best[height<={h}]/best"
        )
    # >=1440p OR mkv container: grab best at that height regardless of codec
    return f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"


def _postprocessors(format_group, format_ext, audio_bitrate, with_thumbnail_embed, want_thumbnail):
    """Build the yt-dlp postprocessor chain for this download.

    Chain order matters -- FFmpegThumbnailsConvertor has to run BEFORE
    EmbedThumbnail so the embedder picks up the .jpg (not .webp which
    some containers reject). Separately, if the user asked for a sidecar
    thumbnail, we convert to .jpg as their expected format regardless
    of what YouTube served (usually .webp these days).

    FFmpegExtractAudio + FFmpegThumbnailsConvertor only need ffmpeg.
    FFmpegMetadata + EmbedThumbnail need ffprobe too (hence HAS_FFPROBE
    gate on the embed pair -- install ffmpeg/ffprobe via fetch_ffmpeg.bat
    to enable metadata tagging + cover art in the media file).
    """
    pps = []
    # Audio extraction to user's chosen codec + bitrate (ffmpeg only).
    if format_group == "audio":
        pp = {"key": "FFmpegExtractAudio", "preferredcodec": format_ext}
        if format_ext not in LOSSLESS_AUDIO:
            pp["preferredquality"] = str(audio_bitrate or 192)
        pps.append(pp)
    # Convert the downloaded thumbnail (usually .webp) to .jpg so the
    # sidecar file matches the UI's "Save thumbnail (.jpg)" label AND
    # so EmbedThumbnail has a compatible format to embed.
    if want_thumbnail or with_thumbnail_embed:
        pps.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
    # Metadata + thumbnail embed both need ffprobe
    if HAS_FFPROBE:
        pps.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True})
        if with_thumbnail_embed:
            # already_have_thumbnail=True because FFmpegThumbnailsConvertor
            # already produced the .jpg. This also keeps the sidecar
            # around after embedding instead of auto-deleting it.
            pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": True})
    return pps


def probe_url(url):
    """Extract metadata for a URL without downloading. Returns title,
    thumbnail, duration, channel, video_id, and a short list of available
    quality tiers (so the UI only shows options that actually exist)."""
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    # Pull out heights that are actually available so we don't offer 1080p
    # for a video only uploaded at 720p.
    heights = set()
    for f in info.get("formats") or []:
        h = f.get("height")
        if h:
            heights.add(int(h))
    available = sorted([h for h in heights if h in (360, 480, 720, 1080, 1440, 2160)], reverse=True)
    return {
        "id": info.get("id"),
        "title": info.get("title") or "Untitled",
        "channel": info.get("uploader") or info.get("channel") or "Unknown",
        "duration": info.get("duration") or 0,
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
        "available_heights": available,
    }


def _unique_stem(title, video_id, ext):
    """Pick a SUBFOLDER NAME under downloads/ that's unique across the
    app's ENTIRE lifetime -- not just what's currently on disk. Check
    against:
      1. Existing folders under downloads/
      2. All history.json entries
      3. All activity.json entries (deleted but logged)
    This way, if you download, delete, and re-download the same video,
    the new folder is (2), not a reused original name. Keeps downloads
    distinguishable even across delete cycles.

    Format:
      'Sanitized Title [video_id]'       if never used
      'Sanitized Title [video_id] (2)'   if base has been used
      'Sanitized Title [video_id] (3)'   and so on
    """
    from yt_dlp.utils import sanitize_filename
    safe_title = sanitize_filename(str(title or "Untitled"), restricted=False)
    base = f"{safe_title} [{video_id}]"

    # Build set of already-used names from all three sources.
    used = set()
    try:
        for p in DOWNLOADS_DIR.iterdir():
            if p.is_dir():
                used.add(p.name)
    except Exception:
        pass
    for entry in _load_history() + _load_activity():
        folder = entry.get("folder")
        if folder:
            used.add(Path(folder).name)
        # Fallback for legacy flat-layout entries: derive from filename
        fname = entry.get("filename")
        if fname and not folder:
            stem = Path(fname).stem
            # Drop an extension-masquerading suffix if present (.tmp etc)
            used.add(stem)

    if base not in used:
        return base
    n = 2
    while True:
        candidate = f"{base} ({n})"
        if candidate not in used:
            return candidate
        n += 1


def _do_download(job_id, url, params):
    """Background worker: runs the actual yt-dlp download. Updates the
    in-memory job dict via yt-dlp's progress_hooks so the frontend can poll.

    `params` shape:
        format_group:    "video" | "audio"
        format_ext:      "mp4"|"mkv"|"webm"  OR  "mp3"|"m4a"|"opus"|"wav"|"flac"
        resolution:      str height cap, e.g. "1080" (ignored for audio)
        audio_bitrate:   str kbps (128/192/256/320) -- ignored for lossless
        want_transcript: bool
        want_thumbnail:  bool  -- save .jpg sidecar
    """
    import yt_dlp

    format_group = params.get("format_group", "video")
    format_ext = params.get("format_ext", "mp4")
    resolution = str(params.get("resolution", "1080"))
    audio_bitrate = str(params.get("audio_bitrate", "192"))
    want_transcript = bool(params.get("want_transcript", False))
    want_thumbnail = bool(params.get("want_thumbnail", False))
    want_subtitles = bool(params.get("want_subtitles", False))

    def _hook(d):
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = int((done / total) * 100) if total else 0
            _update_job(
                job_id,
                status="downloading",
                percent=max(0, min(99, pct)),
                speed=d.get("_speed_str", "").strip(),
                eta=d.get("_eta_str", "").strip(),
            )
        elif status == "finished":
            # yt-dlp calls this once per file before post-processing.
            # Hold at 99% until post-processors (merge, metadata embed) finish.
            _update_job(job_id, percent=99, status="downloading", speed="", eta="processing...")

    opts = _yt_opts_base()
    opts["progress_hooks"] = [_hook]
    # Only pre-fetch the thumbnail if either (a) the user wants a sidecar
    # .jpg, or (b) we have ffprobe and can actually embed it into the file.
    # When neither applies, skipping writethumbnail is faster and avoids
    # leaving stray files behind.
    opts["writethumbnail"] = bool(want_thumbnail or HAS_FFPROBE)

    # NOTE: subtitles intentionally NOT wired into the main opts here.
    # yt-dlp treats subtitle fetch failures as fatal, so a bad subtitle
    # track (e.g. HTTP error on the "en-orig" variant) kills the entire
    # video download. Instead we do a separate, isolated subtitle pass
    # AFTER the video lands -- see the post-download block below.

    if format_group == "audio":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = _postprocessors("audio", format_ext, audio_bitrate, with_thumbnail_embed=True, want_thumbnail=want_thumbnail)
    else:
        opts["format"] = _fmt_selector(format_ext, resolution)
        opts["merge_output_format"] = format_ext
        opts["postprocessors"] = _postprocessors("video", format_ext, None, with_thumbnail_embed=True, want_thumbnail=want_thumbnail)

    try:
        # Pre-probe to get title + id so we can pick a non-colliding
        # filename BEFORE yt-dlp starts writing. If we didn't do this and
        # the user re-downloaded the same video, yt-dlp would happily
        # overwrite the existing file. With _unique_stem() we get "Name
        # [id] (2).mp4", "(3)", etc.
        probe_opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
        with yt_dlp.YoutubeDL(probe_opts) as probe_ydl:
            probe_info = probe_ydl.extract_info(url, download=False)
        unique_stem = _unique_stem(
            probe_info.get("title"),
            probe_info.get("id") or "",
            format_ext,
        )
        # Per-video subfolder: all outputs (video, transcript, thumbnail)
        # land in downloads/{unique_stem}/ so the whole set stays together
        # and can be managed as a unit. Folder is created now; yt-dlp's
        # outtmpl routes the media into it; post-download sidecar writes
        # land in the same folder by using the same stem.
        video_folder = DOWNLOADS_DIR / unique_stem
        video_folder.mkdir(parents=True, exist_ok=True)
        opts["outtmpl"] = str(video_folder / f"{unique_stem}.%(ext)s")

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        # Resolve the real final filename. yt-dlp rewrites the extension during
        # post-processing (audio extract / mkv+webm merge); the trustworthy
        # source is the "requested_downloads" list if present, else we swap
        # the extension on the template path.
        with yt_dlp.YoutubeDL(opts) as ydl:
            base_filename = ydl.prepare_filename(info)
        target_ext = "." + format_ext
        filename = str(Path(base_filename).with_suffix(target_ext))
        if not Path(filename).exists():
            # Fallback: sniff for any file with the right stem + expected ext
            candidate = Path(base_filename)
            for f in candidate.parent.glob(candidate.stem + ".*"):
                if f.suffix.lower() == target_ext:
                    filename = str(f)
                    break
            else:
                filename = base_filename  # last resort

        # Thumbnail sidecar handling. yt-dlp wrote a <stem>.webp/.jpg next to
        # the media file so EmbedThumbnail had something to work with. If the
        # user wants a sidecar copy we keep it (normalizing to .jpg); if not
        # we delete it to avoid clutter.
        thumbnail_path = None
        media_stem = Path(filename).with_suffix("")
        candidates = []
        for ext in (".webp", ".jpg", ".jpeg", ".png"):
            p = Path(str(media_stem) + ext)
            if p.exists():
                candidates.append(p)
        if want_thumbnail and candidates:
            # Prefer jpg if present, else webp
            pick = next((c for c in candidates if c.suffix.lower() in (".jpg", ".jpeg")), candidates[0])
            thumbnail_path = str(pick)
            # Remove the extras (we only need one sidecar)
            for c in candidates:
                if str(c) != thumbnail_path:
                    try: c.unlink()
                    except Exception: pass
        elif candidates:
            # User didn't ask for thumbnail sidecar -- clean them all up
            for c in candidates:
                try: c.unlink()
                except Exception: pass

        # Subtitle sidecar (optional). Run in its OWN yt-dlp invocation
        # with skip_download=True, and in a try/except so any failure
        # (HTTP error, missing track, unsupported language code) is
        # silent and does NOT corrupt the already-successful video
        # download. yt-dlp's default behavior of treating subtitle
        # errors as fatal is exactly what we're isolating against here.
        subtitle_path = None
        if want_subtitles:
            try:
                sub_opts = _yt_opts_base()
                sub_opts["skip_download"] = True
                sub_opts["writesubtitles"] = True
                sub_opts["writeautomaticsub"] = True
                # Narrow language list -- broad patterns like "en.*" pick
                # up broken variants like "en-orig" that fail HTTP.
                sub_opts["subtitleslangs"] = ["en", "en-US", "en-GB"]
                sub_opts["subtitlesformat"] = "srt/best"
                # Output to the same folder with matching stem so our
                # glob below finds them.
                sub_opts["outtmpl"] = str(video_folder / f"{unique_stem}.%(ext)s")
                sub_opts["ignoreerrors"] = True   # belt-and-suspenders
                with yt_dlp.YoutubeDL(sub_opts) as sub_ydl:
                    sub_ydl.download([url])
                srts = sorted(
                    video_folder.glob(f"{unique_stem}*.srt"),
                    key=lambda p: (len(p.name), p.name),
                )
                if srts:
                    subtitle_path = str(srts[0].resolve())
            except Exception as se:
                print(f"[yt-grab] subtitle fetch skipped: {se}", file=sys.stderr)

        # Transcript (optional) -- save as .txt alongside the video.
        transcript_path = None
        video_id = info.get("id")
        if want_transcript:
            try:
                txt = fetch_transcript_text(video_id)
                if txt:
                    transcript_path = str(Path(filename).with_suffix(".txt"))
                    with open(transcript_path, "w", encoding="utf-8") as f:
                        f.write(txt)
            except Exception as te:
                print(f"[yt-dl] transcript failed for {video_id}: {te}", file=sys.stderr)

        _update_job(
            job_id,
            status="done",
            percent=100,
            filename=filename,
            transcript_path=transcript_path,
            thumbnail_path=thumbnail_path,
            subtitle_path=subtitle_path,
            speed="",
            eta="",
        )
        # Download duration: completed - started, in seconds. Gives us
        # a "downloaded in 42s" chip in history for an at-a-glance sense
        # of how heavy each download was.
        completed_dt = datetime.now()
        try:
            started_dt = datetime.fromisoformat(jobs[job_id]["started_at"])
            duration_seconds = max(0, int((completed_dt - started_dt).total_seconds()))
        except Exception:
            duration_seconds = None
        _add_to_history({
            "id": job_id,
            "title": info.get("title") or "Untitled",
            "channel": info.get("uploader") or info.get("channel") or "Unknown",
            "folder": str(video_folder),
            "filename": filename,
            "transcript_path": transcript_path,
            "thumbnail_path": thumbnail_path,
            "subtitle_path": subtitle_path,
            "format_group": format_group,
            "format_ext": format_ext,
            "resolution": "—" if format_group == "audio" else str(resolution),
            "audio_bitrate": audio_bitrate if format_group == "audio" and format_ext not in LOSSLESS_AUDIO else None,
            "completed_at": completed_dt.isoformat(timespec="seconds"),
            "duration_seconds": duration_seconds,
            "source_url": url,
            "video_id": video_id,
        })
    except Exception as e:
        _update_job(job_id, status="error", error=str(e))


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------
_YT_ID_RE = re.compile(r"(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})")


def _video_id_from_url(url):
    m = _YT_ID_RE.search(url or "")
    return m.group(1) if m else None


def fetch_transcript_text(video_id):
    """Return plaintext transcript (line-per-caption) or empty string if
    no transcript is available. Prefers English; falls back to the first
    available language yt provides."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except Exception:
        return ""
    if not video_id:
        return ""
    try:
        # Newer youtube-transcript-api exposes instance methods; older
        # versions have a classmethod. Handle both to avoid a hard pin.
        try:
            api = YouTubeTranscriptApi()
            listed = api.list(video_id)
            transcript = None
            try:
                transcript = listed.find_transcript(["en", "en-US", "en-GB"])
            except Exception:
                for t in listed:
                    transcript = t
                    break
            data = transcript.fetch() if transcript else []
        except Exception:
            data = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US", "en-GB"])
        lines = []
        for row in data:
            text = row.get("text") if isinstance(row, dict) else getattr(row, "text", "")
            if text:
                lines.append(text.strip())
        return "\n".join(lines)
    except Exception as e:
        print(f"[yt-dl] transcript fetch error: {e}", file=sys.stderr)
        return ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    # When frozen, index.html is bundled inside the PyInstaller extraction
    # dir (RESOURCE_DIR); in dev it's next to the script.
    return send_from_directory(str(RESOURCE_DIR), "index.html")


@app.route("/favicon.ico")
def favicon():
    """Serve icon.ico as the browser tab favicon -- matches the console
    window icon and the exe icon for a consistent look across surfaces.
    If no icon.ico is shipped, return 204 so the browser stops asking."""
    for candidate in (RESOURCE_DIR / "icon.ico", BASE_DIR / "icon.ico"):
        if candidate.exists():
            return send_from_directory(str(candidate.parent), candidate.name, mimetype="image/x-icon")
    return ("", 204)


@app.route("/icon.png")
def icon_png():
    """Serve the source PNG icon. Chrome's app-mode window prefers a
    large PNG over a multi-res ICO for the window icon + taskbar icon,
    so exposing the raw PNG gets us a crisper result."""
    for candidate in (RESOURCE_DIR / "icon.png", BASE_DIR / "icon.png"):
        if candidate.exists():
            return send_from_directory(str(candidate.parent), candidate.name, mimetype="image/png")
    return ("", 204)


@app.route("/manifest.json")
def manifest_json():
    """Web app manifest -- tells Chrome/Edge this is a standalone app,
    which makes them commit to the icon we provide (not fall back to
    a generic globe) when running in --app mode."""
    return jsonify({
        "name": "YT Grab",
        "short_name": "YT Grab",
        "description": "Local. Private. Premium.",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0b0b0f",
        "theme_color": "#8a5cf6",
        "icons": [
            {"src": "/icon.png", "sizes": "any", "type": "image/png", "purpose": "any"},
            {"src": "/favicon.ico", "sizes": "16x16 32x32 48x48 64x64 128x128 256x256", "type": "image/x-icon"},
        ],
    })


@app.route("/api/info", methods=["POST"])
def api_info():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "missing url"}), 400
    try:
        meta = probe_url(url)
        return jsonify({"ok": True, "info": meta})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/playlist_info", methods=["POST"])
def api_playlist_info():
    """Fetch a FLAT playlist listing: just titles + IDs + thumbnails for
    each entry, no per-video format probe. Much faster than calling
    /api/info on each individual URL -- a 50-video playlist comes back
    in ~1 second instead of ~30.

    Response shape:
      {
        ok: True,
        playlist_title: "...",
        playlist_uploader: "...",
        entries: [
          { id, title, url, duration, thumbnail },
          ...
        ]
      }
    """
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "missing url"}), 400
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",  # title + id only, no format probe
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return jsonify({"ok": False, "error": "empty response"}), 500
        entries_raw = info.get("entries") or []
        if not entries_raw:
            return jsonify({"ok": False, "error": "no entries in playlist"}), 400
        entries = []
        for e in entries_raw:
            if not e:
                continue
            vid = e.get("id") or ""
            # Build the canonical watch URL -- flat_playlist gives us
            # id but not always a usable url field.
            watch_url = e.get("url") or (
                f"https://www.youtube.com/watch?v={vid}" if vid else ""
            )
            entries.append({
                "id": vid,
                "title": e.get("title") or "Untitled",
                "url": watch_url,
                "duration": e.get("duration"),
                "thumbnail": e.get("thumbnail") or (
                    f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else None
                ),
            })
        return jsonify({
            "ok": True,
            "playlist_title": info.get("title") or "Playlist",
            "playlist_uploader": info.get("uploader") or info.get("channel") or "",
            "entries": entries,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "missing url"}), 400

    # Normalize the format -- frontend sends format_group + format_ext.
    # Default to mp4 @ 1080p if nothing specified.
    format_ext = (data.get("format_ext") or "mp4").lower()
    if format_ext in AUDIO_FORMATS:
        format_group = "audio"
    elif format_ext in VIDEO_FORMATS:
        format_group = "video"
    else:
        return jsonify({"ok": False, "error": f"unsupported format: {format_ext}"}), 400

    params = {
        "format_group": format_group,
        "format_ext": format_ext,
        "resolution": str(data.get("resolution") or "1080"),
        "audio_bitrate": str(data.get("audio_bitrate") or "192"),
        "want_transcript": bool(data.get("want_transcript")),
        "want_thumbnail": bool(data.get("want_thumbnail")),
        "want_subtitles": bool(data.get("want_subtitles")),
    }
    title_hint = (data.get("title") or "Untitled").strip()
    job_id = _new_job(title_hint)
    t = threading.Thread(target=_do_download, args=(job_id, url, params), daemon=True)
    t.start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/history/delete/<job_id>", methods=["POST"])
def api_history_delete(job_id):
    """Soft delete: remove from History AND move the folder from
    downloads/ to previous_downloads/. Entry is logged to activity.json
    (Previous Downloads card) and pushed onto the undo stack so Ctrl+Z
    restores it. Files are NOT sent to the Recycle Bin at this stage --
    that happens only if the user subsequently deletes from Previous."""
    hist = _load_history()
    victim = None
    new_hist = []
    for h in hist:
        if h.get("id") == job_id and victim is None:
            victim = h
        else:
            new_hist.append(h)
    if not victim:
        return jsonify({"ok": False, "error": "not in history"}), 404
    _soft_delete_to_previous(victim)   # mutates victim paths in-place
    _save_history(new_hist)
    _log_activity(victim)              # logs the UPDATED victim (new paths)
    with _undo_lock:
        _undo_stack.append([victim])
        while len(_undo_stack) > UNDO_STACK_MAX:
            _undo_stack.pop(0)
    return jsonify({"ok": True})


@app.route("/api/history/delete_sidecar/<job_id>/<kind>", methods=["POST"])
def api_history_delete_sidecar(job_id, kind):
    """Delete JUST ONE sidecar file without touching the main video.
    The history row stays; only the relevant path field is cleared so
    the UI hides the button. File goes to the Recycle Bin like the
    main delete does.

    kind: "transcript" -> .txt sidecar
          "thumbnail"  -> .jpg sidecar
          "subtitle"   -> .srt sidecar
    """
    if kind not in ("transcript", "thumbnail", "subtitle"):
        return jsonify({"ok": False, "error": "invalid kind"}), 400
    key = {
        "transcript": "transcript_path",
        "thumbnail":  "thumbnail_path",
        "subtitle":   "subtitle_path",
    }[kind]

    hist = _load_history()
    target_entry = None
    for entry in hist:
        if entry.get("id") == job_id:
            target_entry = entry
            break
    if not target_entry:
        return jsonify({"ok": False, "error": "not in history"}), 404

    path = target_entry.get(key)
    if path:
        _trash_file(path)
    target_entry[key] = None
    _save_history(hist)
    # Individual sidecar deletes don't push onto the undo stack -- the
    # file's in the Recycle Bin if the user needs it. Keeps the undo
    # semantics about the "big" operations (history entry vanishing).
    return jsonify({"ok": True})


@app.route("/api/history/rename/<job_id>", methods=["POST"])
def api_history_rename(job_id):
    """Rename a history entry -- both the visible title AND the files
    on disk. Rewrites:
      - the per-video folder name under downloads/
      - the main video file
      - the transcript .txt sidecar (if present)
      - the thumbnail .jpg sidecar (if present)
    ... all using _unique_stem() so the new name doesn't collide with
    anything already used (including other videos + deleted history).
    Entry's title is updated so the UI shows the new name immediately.

    Body: { "title": "new title" }
    Returns the updated entry so the client can re-render just that row.
    """
    body = request.get_json(silent=True) or {}
    new_title_raw = (body.get("title") or "").strip()
    if not new_title_raw:
        return jsonify({"ok": False, "error": "empty title"}), 400
    # Cap length so someone doesn't paste a novel and hit Windows MAX_PATH.
    if len(new_title_raw) > 180:
        new_title_raw = new_title_raw[:180]

    hist = _load_history()
    target = None
    for h in hist:
        if h.get("id") == job_id:
            target = h
            break
    if not target:
        return jsonify({"ok": False, "error": "not in history"}), 404

    # Nothing to do if the title didn't actually change. Short-circuit
    # so we don't churn the filesystem for a no-op edit.
    if (target.get("title") or "").strip() == new_title_raw:
        return jsonify({"ok": True, "entry": target, "unchanged": True})

    video_id = target.get("video_id") or "unknown"
    old_folder = target.get("folder")
    if not old_folder or not Path(old_folder).exists():
        # Legacy / already-deleted entries: just update the display title.
        target["title"] = new_title_raw
        _save_history(hist)
        return jsonify({"ok": True, "entry": target, "files_renamed": False})

    old_folder_path = Path(old_folder)
    old_stem = old_folder_path.name  # "Title [id]" or "Title [id] (2)"

    # Compute a new collision-free stem. _unique_stem walks history +
    # activity so we won't stomp any other entry. BUT -- it'll include
    # OUR OWN old stem in the "used" set, so temporarily drop this
    # entry's folder from the used set by asking for the new base and
    # letting the suffix logic pick (2) if needed.
    try:
        new_stem = _unique_stem(new_title_raw, video_id, "")
    except Exception as e:
        return jsonify({"ok": False, "error": f"name compute failed: {e}"}), 500

    # If _unique_stem picked exactly our old stem (because our own
    # folder is still on disk and the title happens to sanitize to the
    # same base), that's a no-op on disk.
    if new_stem == old_stem:
        target["title"] = new_title_raw
        _save_history(hist)
        return jsonify({"ok": True, "entry": target, "files_renamed": False})

    new_folder_path = old_folder_path.parent / new_stem

    # Rename the folder. If it fails (e.g. an Explorer window has it
    # pinned open), bail before touching any file paths.
    try:
        old_folder_path.rename(new_folder_path)
    except Exception as e:
        return jsonify({"ok": False, "error": f"folder rename failed: {e}"}), 500

    # Rename every file inside so the file stems match the new folder
    # stem. Files were saved as "{stem}.{ext}" at download time, so
    # anything starting with old_stem needs its stem swapped.
    renamed_map = {}  # old_abs_path -> new_abs_path (for entry fixup)
    try:
        for child in new_folder_path.iterdir():
            if not child.is_file():
                continue
            if child.stem == old_stem:
                new_child = child.with_name(new_stem + child.suffix)
                # Collision-safe -- if somehow a file already has that
                # name, append a numeric suffix.
                counter = 2
                while new_child.exists():
                    new_child = child.with_name(f"{new_stem} ({counter}){child.suffix}")
                    counter += 1
                old_abs = str(child.resolve())
                child.rename(new_child)
                renamed_map[old_abs] = str(new_child.resolve())
            else:
                # File with a different stem (shouldn't happen in
                # current layout but handle gracefully). Leave it.
                pass
    except Exception as e:
        # Partial-rename state is possible here. Best-effort: rename
        # the folder back if we can, so state is at least consistent.
        try: new_folder_path.rename(old_folder_path)
        except Exception: pass
        return jsonify({"ok": False, "error": f"file rename failed: {e}"}), 500

    # Rewrite the history entry's stored paths.
    def _remap(p):
        if not p: return p
        try:
            abs_p = str(Path(p).resolve())
        except Exception:
            abs_p = p
        if abs_p in renamed_map:
            return renamed_map[abs_p]
        # Fallback: path-replace old_stem -> new_stem in the string.
        # Handles any slight path normalization mismatches.
        return p.replace(old_stem, new_stem)

    target["title"] = new_title_raw
    target["folder"] = str(new_folder_path)
    target["filename"] = _remap(target.get("filename"))
    target["transcript_path"] = _remap(target.get("transcript_path"))
    target["thumbnail_path"] = _remap(target.get("thumbnail_path"))
    target["subtitle_path"] = _remap(target.get("subtitle_path"))
    _save_history(hist)

    return jsonify({"ok": True, "entry": target, "files_renamed": True})


@app.route("/api/history/clear", methods=["POST"])
def api_history_clear():
    """Clear the whole history list -- soft delete. Every folder is
    moved from downloads/ to previous_downloads/ (not the Recycle Bin).
    User can still permanently drop them by clearing Previous, or
    restore via Ctrl+Z."""
    hist = _load_history()
    for entry in hist:
        _soft_delete_to_previous(entry)
    _save_history([])
    if hist:
        _log_activity(hist)
        with _undo_lock:
            _undo_stack.append(list(hist))
            while len(_undo_stack) > UNDO_STACK_MAX:
                _undo_stack.pop(0)
    return jsonify({"ok": True, "count": len(hist)})


@app.route("/api/activity", methods=["GET"])
def api_activity():
    """Return the Previous Downloads log -- everything the user has
    downloaded and then deleted. Most recent first."""
    return jsonify({"ok": True, "activity": list(reversed(_load_activity()))[:100]})


@app.route("/api/activity/delete/<job_id>", methods=["POST"])
def api_activity_delete(job_id):
    """Hard delete: if the entry's folder still lives in
    previous_downloads/, send it to the Recycle Bin for good. Then
    remove from the activity log. This is the final stage of the
    two-step delete -- after this, recovery requires digging into
    the Windows Recycle Bin."""
    activity = _load_activity()
    victim = None
    new_activity = []
    for a in activity:
        if a.get("id") == job_id and victim is None:
            victim = a
        else:
            new_activity.append(a)
    if not victim:
        return jsonify({"ok": False, "error": "not in activity"}), 404
    # If the soft-delete folder still exists, trash it now.
    _trash_all_sidecars(victim)
    _save_activity(new_activity)
    return jsonify({"ok": True})


@app.route("/api/activity/clear", methods=["POST"])
def api_activity_clear():
    """Hard-delete EVERY entry in Previous Downloads: any folders still
    in previous_downloads/ go to the Recycle Bin, then the log itself
    is wiped."""
    activity = _load_activity()
    for entry in activity:
        _trash_all_sidecars(entry)
    _save_activity([])
    return jsonify({"ok": True, "count": len(activity)})


@app.route("/api/history/undo", methods=["POST"])
def api_history_undo():
    """Pop the most recent delete-op and restore its history entries.
    Now that delete is a soft move to previous_downloads/, undo also
    moves the folder BACK into downloads/ and removes the mirrored
    entry from activity.json -- a full round-trip."""
    with _undo_lock:
        if not _undo_stack:
            return jsonify({"ok": False, "error": "nothing to undo"}), 404
        batch = _undo_stack.pop()
    hist = _load_history()
    existing_ids = {h.get("id") for h in hist}
    # Move files back from previous_downloads/ to downloads/ and
    # drop the mirrored entries out of activity.json.
    activity = _load_activity()
    restored_ids = set()
    for entry in batch:
        _restore_from_previous(entry)    # mutates paths back to downloads/
        if entry.get("id") not in existing_ids:
            hist.append(entry)
            restored_ids.add(entry.get("id"))
    if restored_ids:
        activity = [a for a in activity if a.get("id") not in restored_ids]
        _save_activity(activity)
    _save_history(hist)
    return jsonify({"ok": True, "restored": len(batch)})


# ---------------------------------------------------------------------------
# Heartbeat + shutdown
# ---------------------------------------------------------------------------
def _any_jobs_active():
    with jobs_lock:
        return any(j.get("status") in ("pending", "downloading") for j in jobs.values())


@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    """Browser pings this every 20s. We update the last-seen timestamp;
    the monitor thread uses it to decide when to exit."""
    global _last_heartbeat
    with _heartbeat_lock:
        _last_heartbeat = time.time()
    return jsonify({"ok": True})


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """Fired by the frontend's beforeunload handler when the user closes
    the last tab. Sets the pending-shutdown flag; the monitor thread
    handles the actual exit once jobs (if any) finish."""
    global _pending_shutdown
    _pending_shutdown = True
    return jsonify({"ok": True})


@app.route("/api/read_transcript", methods=["POST"])
def api_read_transcript():
    """Read a transcript .txt file that was saved alongside a previous
    download. History rows use this to re-open transcripts without
    re-hitting YouTube -- avoids the 'no transcript available' error
    that happened when the URL wasn't loaded in the current session."""
    data = request.get_json(silent=True) or {}
    path = data.get("path") or ""
    if not path:
        return jsonify({"ok": False, "error": "missing path"}), 400
    p = Path(path)
    if not p.exists():
        return jsonify({"ok": False, "error": "transcript file missing"}), 404
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return jsonify({"ok": True, "text": f.read()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _heartbeat_monitor():
    """Background thread: watches for missed heartbeats + explicit
    shutdown requests, exits the process when it's safe (no jobs running).
    Runs forever with a short poll interval; cheap because it's mostly
    sleeping."""
    while True:
        time.sleep(HEARTBEAT_POLL_SEC)
        with _heartbeat_lock:
            elapsed = time.time() - _last_heartbeat
        should_exit = False
        reason = ""
        if _pending_shutdown and not _any_jobs_active():
            should_exit = True
            reason = "browser closed, no active jobs"
        elif elapsed > HEARTBEAT_TIMEOUT_SEC and not _any_jobs_active():
            should_exit = True
            reason = f"no heartbeat for {int(elapsed)}s, no active jobs"
        if should_exit:
            try:
                print(f"[yt-dl] shutting down ({reason})")
            except Exception:
                pass
            # os._exit is the only reliable way to stop Flask's dev server
            # from a background thread. It kills the whole process.
            os._exit(0)


def _port_in_use(port):
    """True if something is already listening on localhost:port."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False
    finally:
        try: s.close()
        except Exception: pass


@app.route("/api/progress/<job_id>", methods=["GET"])
def api_progress(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "unknown job"}), 404
        return jsonify({"ok": True, "job": dict(job)})


@app.route("/api/progress_all", methods=["POST"])
def api_progress_all():
    """Batch progress endpoint -- frontend sends a list of active job
    IDs, server returns all their progress in one response. Much more
    efficient than polling each job individually: with 20 active
    downloads, we go from 33 req/sec (20 jobs * 1/600ms) down to
    1 req/sec no matter how many are running. That frees Flask up to
    respond to /api/download instantly even under heavy load."""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    out = {}
    with jobs_lock:
        for jid in ids:
            j = jobs.get(jid)
            if j is not None:
                out[jid] = dict(j)
    return jsonify({"ok": True, "jobs": out})


@app.route("/api/transcript", methods=["POST"])
def api_transcript():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    vid = _video_id_from_url(url)
    if not vid:
        return jsonify({"ok": False, "error": "could not extract video id"}), 400
    text = fetch_transcript_text(vid)
    if not text:
        return jsonify({"ok": False, "error": "no transcript available for this video"})
    return jsonify({"ok": True, "text": text, "video_id": vid})


@app.route("/api/history", methods=["GET"])
def api_history():
    # Return everything -- the UI has its own scrollable card + filter.
    # The cap of 20 was a legacy holdover from the very first version
    # when history rendered as a flat non-scrolling list. Soft-cap at
    # 1000 to avoid shipping absurd JSON payloads if someone has a
    # truly massive library; the UI wouldn't paint 1000+ rows well
    # anyway and the user should Clear all if it ever got that big.
    return jsonify({"ok": True, "history": list(reversed(_load_history()))[:1000]})


def _center_window(hwnd, width=1200, height=800):
    """Move and resize the given window so it's centered on the primary
    monitor. Used for freshly-spawned Explorer windows -- Windows
    remembers the last position, which is often off-center or offscreen
    on multi-monitor setups."""
    if not hwnd:
        return
    try:
        import ctypes
        SM_CXSCREEN, SM_CYSCREEN = 0, 1
        sw = ctypes.windll.user32.GetSystemMetrics(SM_CXSCREEN)
        sh = ctypes.windll.user32.GetSystemMetrics(SM_CYSCREEN)
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2)
        # SWP_NOZORDER = 0x4, SWP_SHOWWINDOW = 0x40
        ctypes.windll.user32.SetWindowPos(hwnd, 0, x, y, width, height, 0x4 | 0x40)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Single-window Explorer tracking (v1.11.2)
# ---------------------------------------------------------------------------
# We remember the HWND of the Explorer window WE spawned. On the next
# open-folder click, if that HWND is still alive, we close it before
# spawning a new one -- guaranteeing YT Grab never leaves more than one
# Explorer window on screen at a time. User-opened Explorer windows
# (Documents, Desktop, etc.) are never touched -- we only close our
# own tracked HWND.
_our_explorer_hwnd = None
_our_explorer_lock = threading.Lock()


def _is_window_alive(hwnd):
    """True if hwnd still refers to a live top-level window."""
    if not hwnd:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.IsWindow(hwnd))
    except Exception:
        return False


def _close_hwnd(hwnd, wait_ms=400):
    """Post WM_CLOSE to hwnd and wait up to wait_ms for it to die."""
    if not hwnd:
        return
    try:
        import ctypes
        WM_CLOSE = 0x0010
        ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        deadline = time.time() + (wait_ms / 1000.0)
        while time.time() < deadline:
            if not _is_window_alive(hwnd):
                return
            time.sleep(0.02)
    except Exception:
        pass


def _set_our_explorer(hwnd):
    """Record hwnd as the Explorer window we own."""
    global _our_explorer_hwnd
    with _our_explorer_lock:
        _our_explorer_hwnd = hwnd


def _close_our_explorer():
    """If we're tracking an Explorer window and it's still alive, close
    it. Clears the tracked HWND either way."""
    global _our_explorer_hwnd
    with _our_explorer_lock:
        hwnd = _our_explorer_hwnd
        _our_explorer_hwnd = None
    if hwnd and _is_window_alive(hwnd):
        _close_hwnd(hwnd)


def _activate_explorer_hwnd(hwnd, center=False):
    """Bring an Explorer window to foreground. If center=True, also
    resize+center it on the primary monitor (for newly-spawned windows)."""
    if not hwnd:
        return
    try:
        import ctypes
        # Alt-keystroke trick unlocks focus-stealing prevention for this process
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x12, 0, 0x0002, 0)
        # SW_RESTORE = 9 (un-minimize if minimized)
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        ctypes.windll.user32.BringWindowToTop(hwnd)
    except Exception:
        pass
    if center:
        _center_window(hwnd)


def _find_explorer_hwnd_for_path(folder_path):
    """Scan Shell.Application.Windows() for an Explorer window at
    folder_path. Returns the HWND if found, or None. COM must be
    initialized on the calling thread."""
    try:
        import comtypes.client
        from urllib.parse import urlparse, unquote
        shell = comtypes.client.CreateObject("Shell.Application")
        target_norm = os.path.normcase(os.path.normpath(str(folder_path)))
        windows = shell.Windows()
        count = windows.Count
        for i in range(count):
            try:
                window = windows.Item(i)
                if window is None:
                    continue
                url = (window.LocationURL or "")
                if not url.lower().startswith("file:"):
                    continue
                parsed = urlparse(url)
                path = unquote(parsed.path).lstrip("/").replace("/", "\\")
                if os.path.normcase(os.path.normpath(path)) == target_norm:
                    return int(window.HWND)
            except Exception:
                continue
    except Exception:
        pass
    return None


def _focus_existing_explorer(folder_path):
    """Reuse an existing Explorer window at folder_path. Returns True if
    one was found + activated. Caller spawns a new window if False.

    Flask worker threads haven't initialized COM -- we do it here."""
    if not sys.platform.startswith("win"):
        return False
    import ctypes
    try:
        # COINIT_APARTMENTTHREADED = 0x2. Returns S_FALSE (1) if already
        # initialized, which is fine -- we proceed either way.
        ctypes.windll.ole32.CoInitializeEx(None, 0x2)
    except Exception:
        pass
    hwnd = _find_explorer_hwnd_for_path(folder_path)
    if hwnd:
        _activate_explorer_hwnd(hwnd, center=False)
        return True
    return False


def _is_win11():
    """True if Windows 11 (build >= 22000). File Explorer gained tabs
    in the Moment 1 update (build 22621) -- we gate the tab-via-
    keyboard path behind this so Win10 users still get new windows."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import platform
        parts = platform.version().split(".")
        return len(parts) >= 3 and int(parts[2]) >= 22000
    except Exception:
        return False


def _find_any_explorer_hwnd():
    """First HWND of any open file:// Explorer window. Used to pile a
    new tab onto an existing window instead of spawning a fresh one.
    COM must be initialized on the calling thread."""
    try:
        import comtypes.client
        shell = comtypes.client.CreateObject("Shell.Application")
        windows = shell.Windows()
        count = windows.Count
        for i in range(count):
            try:
                w = windows.Item(i)
                if w is None:
                    continue
                if (w.LocationURL or "").lower().startswith("file:"):
                    return int(w.HWND)
            except Exception:
                continue
    except Exception:
        pass
    return None


def _clipboard_get_text():
    """Read CF_UNICODETEXT off the clipboard, or None."""
    try:
        import ctypes
        CF_UNICODETEXT = 13
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        if not u32.OpenClipboard(0):
            return None
        try:
            h = u32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return None
            ptr = k32.GlobalLock(h)
            if not ptr:
                return None
            try:
                return ctypes.wstring_at(ptr)
            finally:
                k32.GlobalUnlock(h)
        finally:
            u32.CloseClipboard()
    except Exception:
        return None


def _clipboard_set_text(text):
    """Stash Unicode text on the clipboard. Returns True on success."""
    try:
        import ctypes
        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        if not u32.OpenClipboard(0):
            return False
        try:
            u32.EmptyClipboard()
            data = (text + "\0").encode("utf-16-le")
            handle = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not handle:
                return False
            ptr = k32.GlobalLock(handle)
            if not ptr:
                return False
            ctypes.memmove(ptr, data, len(data))
            k32.GlobalUnlock(handle)
            u32.SetClipboardData(CF_UNICODETEXT, handle)
            return True
        finally:
            u32.CloseClipboard()
    except Exception:
        return False


def _send_key_combo(modifiers, vk):
    """Send a Win32 key combo via keybd_event. `modifiers` is a list of
    VK codes pressed before `vk` and released after in reverse order."""
    import ctypes
    KEYEVENTF_KEYUP = 0x0002
    u32 = ctypes.windll.user32
    for m in modifiers:
        u32.keybd_event(m, 0, 0, 0)
    u32.keybd_event(vk, 0, 0, 0)
    u32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    for m in reversed(modifiers):
        u32.keybd_event(m, 0, KEYEVENTF_KEYUP, 0)


# v1.11.1: removed _navigate_existing_explorer (the Navigate2 COM
# approach from v1.11). Win11 File Explorer doesn't implement it
# reliably -- calls either silently no-op, hijack the wrong window,
# or leave Explorer in a state where subsequent spawns also fail.
# If a better "reuse a single window" approach becomes viable, it
# goes here.


def _snapshot_explorer_hwnds():
    """Return a set of HWNDs of every open file: Explorer window. Used
    as a "before" snapshot so we can identify a newly-spawned window
    by diff after ShellExecuteW."""
    result = set()
    try:
        import ctypes
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)
        except Exception:
            pass
        import comtypes.client
        shell = comtypes.client.CreateObject("Shell.Application")
        windows = shell.Windows()
        for i in range(windows.Count):
            try:
                w = windows.Item(i)
                if w is None:
                    continue
                loc = (w.LocationURL or "")
                if loc.lower().startswith("file:"):
                    result.add(int(w.HWND))
            except Exception:
                continue
    except Exception:
        pass
    return result


def _wait_for_new_explorer(before_hwnds, folder_path, timeout_sec=3.0):
    """After spawning a new Explorer window via ShellExecuteW, block
    until it shows up and return its HWND. Prefers a HWND that (a)
    wasn't in `before_hwnds` AND (b) is showing the expected path.
    Falls back to "any new HWND" if path match fails (Explorer may not
    have settled its LocationURL yet). Returns None on timeout."""
    if not sys.platform.startswith("win"):
        return None
    import ctypes
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 0x2)
    except Exception:
        pass
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        # Preferred: new HWND that's at the target path.
        at_path = _find_explorer_hwnd_for_path(folder_path)
        if at_path and at_path not in before_hwnds:
            return at_path
        # Fallback: any brand-new file: Explorer HWND.
        now = _snapshot_explorer_hwnds()
        new = now - before_hwnds
        if new:
            return next(iter(new))
        time.sleep(0.1)
    # Timed out -- last-ditch: accept a same-path hwnd even if it was
    # in before_hwnds (shouldn't happen but keeps behavior graceful).
    return _find_explorer_hwnd_for_path(folder_path)


def _spawn_and_track_explorer(target_path, is_file_reveal=False):
    """Spawn a fresh Explorer window at target_path (or revealing a
    file if is_file_reveal). Blocks briefly until the window appears,
    captures its HWND as _our_explorer_hwnd, activates+centers it.

    The blocking is deliberate -- makes HWND tracking race-free so a
    rapid follow-up click knows which window to close. Typical wait
    is ~200-600ms; capped at 3 seconds."""
    import ctypes
    before = _snapshot_explorer_hwnds()
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)        # VK_MENU down
        ctypes.windll.user32.keybd_event(0x12, 0, 0x0002, 0)   # VK_MENU up
    except Exception:
        pass
    if is_file_reveal:
        params = f'/select,"{target_path}"'
        ctypes.windll.shell32.ShellExecuteW(
            None, "open", "explorer.exe", params, None, 1
        )
        # /select points at the file; the window shows its parent dir.
        poll_path = Path(target_path).parent
    else:
        ctypes.windll.shell32.ShellExecuteW(
            None, "open", str(target_path), None, None, 1
        )
        poll_path = target_path
    new_hwnd = _wait_for_new_explorer(before, poll_path)
    if new_hwnd:
        _activate_explorer_hwnd(new_hwnd, center=True)
        _set_our_explorer(new_hwnd)
    return new_hwnd


@app.route("/api/open_previous_folder", methods=["POST"])
def api_open_previous_folder():
    """Open the dedicated previous_downloads/ folder in Explorer.
    Items soft-deleted from History live here until the user hard
    deletes them from Previous (at which point they go to Recycle
    Bin). Reuses existing Explorer window at that path if one's open,
    same as /api/open_folder does for downloads/."""
    target_path = PREVIOUS_DIR
    try:
        target_path.mkdir(exist_ok=True)   # make sure it's there
        if sys.platform.startswith("win"):
            import ctypes
            # v1.12.1: if ANY Explorer window is already at this exact
            # path, focus it and adopt it as ours. Any previously-
            # tracked window at a DIFFERENT path gets closed so we
            # never leave stragglers around.
            existing_here = _find_explorer_hwnd_for_path(target_path)
            if existing_here:
                prev = _our_explorer_hwnd
                if prev and prev != existing_here and _is_window_alive(prev):
                    _close_hwnd(prev)
                _set_our_explorer(existing_here)
                _activate_explorer_hwnd(existing_here, center=False)
                return jsonify({"ok": True, "reused": True})

            # No window at target. Close our previously-tracked window
            # so we never leave more than one YT-Grab Explorer around,
            # then spawn fresh. Spawn + capture HWND happens SYNCHRO-
            # NOUSLY so a rapid follow-up click knows what to close.
            _close_our_explorer()
            _spawn_and_track_explorer(target_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target_path)])
        else:
            subprocess.Popen(["xdg-open", str(target_path)])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/export_data", methods=["POST"])
def api_export_data():
    """Export selected buckets to a YTGrab-export-<timestamp> folder on
    the Desktop. Same layout as the uninstaller's export so /api/
    import_data can read it back on any install.

    Layout:
        YTGrab-export-<ts>/
            downloads/            (only if include_downloads)
            history.json          (only if include_downloads)
            previous_downloads/   (only if include_previous)
            activity.json         (only if include_previous)

    Body:
        { "include_downloads": bool, "include_previous": bool }

    At least one bucket must be selected.
    """
    data = request.get_json(silent=True) or {}
    inc_dl = bool(data.get("include_downloads", True))
    inc_pv = bool(data.get("include_previous",  True))

    if not (inc_dl or inc_pv):
        return jsonify({
            "ok": False,
            "error": "pick at least one thing to export",
        }), 400

    # Desktop is the natural home for a portable export; fall back to
    # the install folder if the shell profile is weird.
    up = os.environ.get("USERPROFILE", "")
    desktop = Path(up) / "Desktop" if up else None
    root = desktop if (desktop and desktop.is_dir()) else BASE_DIR

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    target = root / f"YTGrab-export-{ts}"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"couldn't create export folder: {e}",
        }), 500

    counts = {"downloads_exported": 0, "previous_exported": 0}

    # History bucket: downloads/ folder + history.json (the index).
    if inc_dl and DOWNLOADS_DIR.is_dir():
        dst_folder = target / "downloads"
        try:
            dst_folder.mkdir(parents=True, exist_ok=True)
            for child in DOWNLOADS_DIR.iterdir():
                if not child.is_dir():
                    continue
                try:
                    shutil.copytree(child, dst_folder / child.name,
                                    dirs_exist_ok=True)
                    counts["downloads_exported"] += 1
                except Exception as e:
                    print(f"[export] copy failed for {child.name}: {e}",
                          file=sys.stderr)
            if HISTORY_FILE.is_file():
                shutil.copy2(HISTORY_FILE, target / "history.json")
        except Exception as e:
            print(f"[export] history bucket failed: {e}", file=sys.stderr)

    # Previous bucket: previous_downloads/ folder + activity.json (log
    # of deleted items so import can restore the Previous panel).
    if inc_pv and PREVIOUS_DIR.is_dir():
        dst_folder = target / "previous_downloads"
        try:
            dst_folder.mkdir(parents=True, exist_ok=True)
            for child in PREVIOUS_DIR.iterdir():
                if not child.is_dir():
                    continue
                try:
                    shutil.copytree(child, dst_folder / child.name,
                                    dirs_exist_ok=True)
                    counts["previous_exported"] += 1
                except Exception as e:
                    print(f"[export] copy failed for {child.name}: {e}",
                          file=sys.stderr)
            if ACTIVITY_FILE.is_file():
                shutil.copy2(ACTIVITY_FILE, target / "activity.json")
        except Exception as e:
            print(f"[export] previous bucket failed: {e}", file=sys.stderr)

    return jsonify({
        "ok": True,
        "path": str(target),
        **counts,
    })


@app.route("/api/import_data", methods=["POST"])
def api_import_data():
    """Import a YTGrab-export-* folder produced by /api/export_data or
    the uninstaller's "Export my data to Desktop" step. Merges
    downloads/, previous_downloads/, history.json, and activity.json
    into the current install.

    Merge strategy: NEVER overwrite. Per-video folders that already
    exist on disk are skipped (the user's current copy wins). JSON
    entries are deduped by job_id so re-importing the same export is
    a no-op.

    Body: { "path": "<folder>" }
    Returns:  { "ok": True, "downloads_added": n, ... } with counts
    for each bucket so the frontend can show a real toast.
    """
    data = request.get_json(silent=True) or {}
    src_raw = (data.get("path") or "").strip()
    if not src_raw:
        return jsonify({"ok": False, "error": "no path provided"}), 400
    src = Path(src_raw)
    if not src.is_dir():
        return jsonify({"ok": False, "error": "not a folder"}), 400

    # Safety: don't import from the running install (would be a no-op
    # at best, data-hazard at worst if shutil.copytree follows weird
    # junction points).
    try:
        if src.resolve() == BASE_DIR.resolve():
            return jsonify({
                "ok": False,
                "error": "can't import from the app's own folder",
            }), 400
    except Exception:
        pass

    # Sanity check the shape before we do any work.
    has_anything = (
        (src / "downloads").is_dir()
        or (src / "previous_downloads").is_dir()
        or (src / "history.json").is_file()
        or (src / "activity.json").is_file()
    )
    if not has_anything:
        return jsonify({
            "ok": False,
            "error": "folder doesn't look like a YT Grab export "
                     "(no downloads/, previous_downloads/, "
                     "history.json, or activity.json)",
        }), 400

    result = {
        "ok": True,
        "downloads_added": 0, "downloads_skipped": 0,
        "previous_added":  0, "previous_skipped":  0,
        "history_added":   0, "history_skipped":   0,
        "activity_added":  0, "activity_skipped":  0,
    }

    # Per-video folders: copy subdirectories that don't already exist.
    for folder_name, added_key, skipped_key, dst_base in (
        ("downloads",          "downloads_added", "downloads_skipped", DOWNLOADS_DIR),
        ("previous_downloads", "previous_added",  "previous_skipped",  PREVIOUS_DIR),
    ):
        src_folder = src / folder_name
        if not src_folder.is_dir():
            continue
        dst_base.mkdir(exist_ok=True)
        for child in src_folder.iterdir():
            if not child.is_dir():
                continue
            dst = dst_base / child.name
            if dst.exists():
                result[skipped_key] += 1
                continue
            try:
                shutil.copytree(child, dst)
                result[added_key] += 1
            except Exception as e:
                print(f"[import] copy failed for {child.name}: {e}",
                      file=sys.stderr)

    # history.json + activity.json: merge, dedupe by job_id.
    for fname, loader, saver, added_key, skipped_key in (
        ("history.json",  _load_history,  _save_history,
         "history_added",  "history_skipped"),
        ("activity.json", _load_activity, _save_activity,
         "activity_added", "activity_skipped"),
    ):
        src_file = src / fname
        if not src_file.is_file():
            continue
        try:
            with open(src_file, "r", encoding="utf-8") as f:
                incoming = json.load(f)
            if not isinstance(incoming, list):
                continue
        except Exception as e:
            print(f"[import] couldn't read {fname}: {e}",
                  file=sys.stderr)
            continue

        current = loader()
        existing_ids = {
            e.get("job_id") for e in current if isinstance(e, dict)
        }
        merged = list(current)
        for entry in incoming:
            if not isinstance(entry, dict):
                continue
            jid = entry.get("job_id")
            if jid and jid in existing_ids:
                result[skipped_key] += 1
                continue
            merged.append(entry)
            if jid:
                existing_ids.add(jid)
            result[added_key] += 1
        saver(merged)

    return jsonify(result)


@app.route("/api/open_folder", methods=["POST"])
def api_open_folder():
    """Reveal a file in Explorer, or open a folder. Reuses an existing
    Explorer window when one is already at that folder (checked via
    Shell.Application COM) -- only spawns a new window when there's
    nothing to focus. Uses the Alt-keystroke Win32 trick to beat
    focus-stealing prevention when the app-mode window holds focus."""
    data = request.get_json(silent=True) or {}
    target = data.get("path") or str(DOWNLOADS_DIR)
    target_path = Path(target)
    if not target_path.exists():
        return jsonify({"ok": False, "error": "path not found"}), 404
    try:
        if sys.platform.startswith("win"):
            import ctypes
            target_folder = target_path.parent if target_path.is_file() else target_path

            # v1.12.1: if ANY Explorer window is already at this folder
            # (ours or user's), focus it and adopt it as ours. A
            # previously-tracked window at a different path gets closed
            # so we don't leave stragglers.
            existing_here = _find_explorer_hwnd_for_path(target_folder)
            if existing_here:
                prev = _our_explorer_hwnd
                if prev and prev != existing_here and _is_window_alive(prev):
                    _close_hwnd(prev)
                _set_our_explorer(existing_here)
                _activate_explorer_hwnd(existing_here, center=False)
                return jsonify({"ok": True, "reused": True})

            # Close our previously-tracked window before spawning a new
            # one (single-window guarantee). Then spawn + capture HWND
            # SYNCHRONOUSLY so a rapid follow-up click can close us.
            _close_our_explorer()
            _spawn_and_track_explorer(
                target_path, is_file_reveal=target_path.is_file()
            )
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R" if target_path.is_file() else "", str(target_path)])
        else:
            subprocess.Popen(["xdg-open", str(target_path.parent if target_path.is_file() else target_path)])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _find_app_mode_browser():
    """Locate Chrome/Edge for --app mode fallback."""
    if not sys.platform.startswith("win"):
        return (None, None)
    candidates = [
        (os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"), "Chrome"),
        (os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"), "Chrome"),
        (os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"), "Chrome"),
        (os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"), "Edge"),
        (os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"), "Edge"),
    ]
    for path, label in candidates:
        if os.path.isfile(path):
            return (path, label)
    return (None, None)


def _fallback_open_browser(url):
    """Chrome app-mode fallback if pywebview fails. Less ideal than
    pywebview (Chrome groups these under 'Chrome' in the taskbar) but
    keeps the app usable on systems where WebView2 isn't available."""
    browser_path, _ = _find_app_mode_browser()
    if browser_path:
        profile_dir = BASE_DIR / ".browser_profile"
        try: profile_dir.mkdir(exist_ok=True)
        except Exception: profile_dir = BASE_DIR
        try:
            subprocess.Popen([
                browser_path, f"--app={url}", "--window-size=1200,900",
                "--new-window", f"--user-data-dir={profile_dir}",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            pass
    try:
        import webbrowser
        webbrowser.open(url)
        return True
    except Exception:
        return False


def _set_shortcut_aumid(lnk_path, app_id):
    """Set AppUserModelID on a .lnk file via IPropertyStore. When the
    shortcut's AppUserModelID matches the launched process's AppUserModelID
    (both SleepyDev.YTGrab.1.0), Windows groups the running window
    UNDER the shortcut's taskbar entry -- pin display inherits the
    shortcut's icon + name, which is what we want."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        from ctypes import wintypes, c_void_p, c_wchar_p

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

        class PROPERTYKEY(ctypes.Structure):
            _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]

        class PROPVARIANT(ctypes.Structure):
            _fields_ = [("vt", ctypes.c_ushort),
                        ("wReserved1", ctypes.c_ushort),
                        ("wReserved2", ctypes.c_ushort),
                        ("wReserved3", ctypes.c_ushort),
                        ("pwszVal", ctypes.c_wchar_p),
                        ("pad", ctypes.c_ulonglong)]
        VT_LPWSTR = 31

        APP_FMTID = GUID(
            0x9F4C2855, 0x9F79, 0x4B39,
            (ctypes.c_ubyte * 8)(0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3)
        )
        PKEY_ID = PROPERTYKEY(APP_FMTID, 5)
        IID_IPropertyStore = GUID(
            0x886D8EEB, 0x8CF2, 0x4446,
            (ctypes.c_ubyte * 8)(0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99)
        )

        ctypes.windll.ole32.CoInitialize(None)

        SHGetPropertyStoreFromParsingName = ctypes.windll.shell32.SHGetPropertyStoreFromParsingName
        SHGetPropertyStoreFromParsingName.argtypes = [
            wintypes.LPCWSTR, c_void_p, wintypes.DWORD,
            ctypes.POINTER(GUID), ctypes.POINTER(c_void_p),
        ]
        SHGetPropertyStoreFromParsingName.restype = ctypes.c_long

        GPS_READWRITE = 2
        pstore = c_void_p()
        hr = SHGetPropertyStoreFromParsingName(
            str(lnk_path), None, GPS_READWRITE,
            ctypes.byref(IID_IPropertyStore), ctypes.byref(pstore),
        )
        if hr < 0 or not pstore:
            return

        vtable = ctypes.cast(pstore, ctypes.POINTER(c_void_p)).contents
        vtable_ptr = ctypes.cast(vtable, ctypes.POINTER(c_void_p * 8))
        SetValueType = ctypes.WINFUNCTYPE(
            ctypes.c_long, c_void_p,
            ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)
        )
        SetValue = SetValueType(vtable_ptr.contents[6])
        CommitType = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p)
        Commit = CommitType(vtable_ptr.contents[7])
        ReleaseType = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)
        Release = ReleaseType(vtable_ptr.contents[2])

        pv = PROPVARIANT()
        pv.vt = VT_LPWSTR
        pv.pwszVal = app_id
        SetValue(pstore, ctypes.byref(PKEY_ID), ctypes.byref(pv))
        Commit(pstore)
        Release(pstore)
    except Exception:
        pass


def _ensure_windows_shortcuts():
    """Create Start Menu + Desktop shortcuts on first run so the app has
    a proper pinnable identity. Without a shortcut, Windows has no
    name/icon source when you pin the running pythonw.exe window and
    falls back to pythonw's metadata ("Python"). With a shortcut, pin
    inherits the .lnk's filename + icon.

    Idempotent: skips if shortcuts already exist. Silently no-ops on
    non-Windows or if PowerShell isn't available."""
    if not sys.platform.startswith("win"):
        return
    # Only create for source-run mode. Frozen exe has its own embedded
    # metadata so shortcuts are unnecessary (and would point at the
    # wrong thing anyway).
    if _IS_FROZEN:
        return

    vbs_target = BASE_DIR / "launch.vbs"
    if not vbs_target.exists():
        return
    icon = BASE_DIR / "icon.ico"
    if not icon.exists():
        return

    start_menu_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    desktop_dir = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
    sm_shortcut = start_menu_dir / "YT Grab.lnk"
    dk_shortcut = desktop_dir / "YT Grab.lnk"

    # -----------------------------------------------------------------
    # Migration: delete any stale "YT Downloader.lnk" shortcuts left
    # over from pre-v1.0 when the product was called YT Downloader.
    # Without this, the user still sees the old label pinned to their
    # taskbar even after the source rename. Idempotent -- if the stale
    # shortcuts don't exist, this is a no-op.
    # -----------------------------------------------------------------
    for legacy in (start_menu_dir / "YT Downloader.lnk",
                   desktop_dir   / "YT Downloader.lnk"):
        try:
            if legacy.exists():
                legacy.unlink()
        except Exception:
            pass  # permissions issue, user will clear it manually

    all_shortcuts = []
    to_create = []
    if start_menu_dir.exists():
        all_shortcuts.append(sm_shortcut)
        if not sm_shortcut.exists():
            to_create.append(sm_shortcut)
    if desktop_dir.exists():
        all_shortcuts.append(dk_shortcut)
        if not dk_shortcut.exists():
            to_create.append(dk_shortcut)

    # Create the .lnk files via WScript.Shell (PowerShell one-liner)
    if to_create:
        ps_lines = ["$W = New-Object -ComObject WScript.Shell"]
        for shortcut in to_create:
            ps_lines.append(f"$S = $W.CreateShortcut('{shortcut}')")
            ps_lines.append(f"$S.TargetPath = '{vbs_target}'")
            ps_lines.append(f"$S.WorkingDirectory = '{BASE_DIR}'")
            ps_lines.append(f"$S.IconLocation = '{icon}'")
            ps_lines.append("$S.Description = 'YT Grab'")
            ps_lines.append("$S.Save()")
        try:
            import subprocess
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "; ".join(ps_lines)],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass

    # ALWAYS stamp the AppUserModelID on every shortcut (new + existing),
    # so shortcuts created by previous versions of server.py without the
    # AUMID get retroactively fixed. Must match the ID the process sets
    # via SetCurrentProcessExplicitAppUserModelID -- that's what makes
    # Windows group the running window under the pinned shortcut.
    for shortcut in all_shortcuts:
        if shortcut.exists():
            _set_shortcut_aumid(shortcut, u"SleepyDev.YTGrab.1.0")


def _set_app_user_model_id():
    """Tell Windows this process is its OWN app, not pythonw. Must be
    called BEFORE any window is created. Format is the Microsoft-
    recommended CompanyName.AppName.Version style -- identical pattern
    to Luna's 'SleepyDev.Luna.1.0', which is what makes Luna's pin
    show as "Luna" with the right icon instead of falling back to
    the pythonw.exe metadata."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            u"SleepyDev.YTGrab.1.0"
        )
    except Exception:
        pass


def _run_flask_bg():
    """Run Flask in a background thread. pywebview takes the main thread
    because its event loop must own the process stdin/stdout on Windows
    for the WebView2 host to work correctly."""
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)


def _launch_pywebview():
    """Primary window: pywebview + WebView2. Mirrors Luna's EXACT
    approach: find the HWND after the window shows, then manually
    LoadImageW + SendMessageW(WM_SETICON) to force the window icon.
    pywebview's own icon= parameter doesn't actually set the window
    icon on the WebView2 backend -- that's why taskbar/pin kept
    showing Python even with AppUserModelID set correctly.

    Windows reads the window icon (via WM_SETICON) for pin display,
    alt-tab, and taskbar grouping. Setting it explicitly is what
    makes Luna show as Luna instead of Python, and what will make
    YT Grab show as YT Grab here."""
    import webview
    icon_path = None
    for candidate in (RESOURCE_DIR / "icon.ico", BASE_DIR / "icon.ico"):
        if candidate.exists():
            icon_path = str(candidate)
            break

    # hidden=True is the key to seamless launch. Window is created but
    # invisible -- we maximize + icon it off-screen, THEN reveal it in
    # one atomic ShowWindow(SW_MAXIMIZE) call. No un-maximized,
    # off-centered flash.
    window = webview.create_window(
        "YT Grab",
        f"http://localhost:{PORT}",
        width=1200,
        height=900,
        min_size=(800, 600),
        background_color="#0b0b0f",
        resizable=True,
        confirm_close=False,
        hidden=True,
        # Frameless: we draw our own title bar in HTML/CSS so the app
        # looks cohesively dark instead of showing the native Windows
        # chrome. Drag handled via -webkit-app-region on the bar;
        # min/max/close buttons wire to the exposed JS API below.
        frameless=True,
        easy_drag=False,   # we use CSS drag regions, not whole-window drag
    )

    # Expose a tiny window-control API so the custom title bar's
    # buttons can minimize / maximize / close the OS window from JS.
    # pywebview.api.* in the frontend calls these methods here.
    def _tb_minimize():
        try: window.minimize()
        except Exception: pass

    def _tb_maximize_toggle():
        # pywebview 5 doesn't expose a toggle, so we drive it directly
        # via Win32. IsZoomed reports current maximized state; SW_RESTORE
        # restores from max, SW_MAXIMIZE re-maximizes. Works whether
        # the window is currently normal or maximized.
        try:
            import ctypes
            hwnd = _find_my_window_hwnd(require_visible=True)
            if not hwnd: return
            SW_RESTORE = 9
            SW_MAXIMIZE = 3
            if ctypes.windll.user32.IsZoomed(hwnd):
                ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
            else:
                ctypes.windll.user32.ShowWindow(hwnd, SW_MAXIMIZE)
        except Exception:
            pass

    def _tb_close():
        try: window.destroy()
        except Exception: pass

    def _tb_drag():
        """Move the window by polling the OS cursor position.

        Background on why this is the third rewrite: WebView2 captures
        the mouse inside its child control so aggressively that
        neither WM_NCLBUTTONDOWN nor WM_SYSCOMMAND(SC_MOVE) routes the
        subsequent mousemove events to the top-level window's move
        loop -- they stay inside WebView2. That's why v1.7.2's
        SendMessage approach broke after restore, and why v1.8.1's
        PostMessage approach also didn't work.

        Solution: skip the Windows system move loop entirely. When JS
        fires mousedown on the title bar, it calls here. We spawn a
        background thread that:
          1. Reads the initial cursor position (GetCursorPos -- reads
             raw OS cursor, unaffected by who holds mouse capture).
          2. Reads the current window rect.
          3. Loops: poll GetAsyncKeyState(VK_LBUTTON). While the left
             button is still down, compute cursor delta, SetWindowPos
             the top-level window to match. ~120Hz.
          4. Exits when the button is released.

        Caveats:
          - No native aero-snap (drag-to-top = maximize, drag-to-edge
            = half-tile). Could be re-added manually via edge detection
            but not a shipping blocker.
          - If the window is maximized, we restore it first so there's
            a draggable target -- matches standard OS behavior (drag
            from max = unmaximize + move with cursor).
        """
        try:
            import ctypes
            from ctypes import wintypes
            hwnd = _find_my_window_hwnd(require_visible=True)
            if not hwnd:
                _debug_log("_tb_drag: no hwnd")
                return

            # Initial cursor position
            start_cursor = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(start_cursor))
            c_x0, c_y0 = start_cursor.x, start_cursor.y

            # If maximized, restore first so there's a sized window to
            # drag. After restore, center the window under the cursor
            # horizontally so it doesn't jump off-screen (mimics how
            # Windows natively unmaximizes on a title-bar drag).
            SW_RESTORE = 9
            if ctypes.windll.user32.IsZoomed(hwnd):
                # Query the restored size before restoring, so we can
                # position the restored window under the cursor.
                ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
                # Give the restore a beat to settle.
                time.sleep(0.02)

            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w_x0, w_y0 = rect.left, rect.top
            win_w = rect.right - rect.left
            win_h = rect.bottom - rect.top

            # If the cursor is now outside the (possibly just-restored)
            # window, re-anchor the window so the cursor stays inside
            # the title-bar area. Guesses cursor at 1/3 width from the
            # left, 16px from top.
            if not (w_x0 <= c_x0 <= w_x0 + win_w and
                    w_y0 <= c_y0 <= w_y0 + win_h):
                w_x0 = c_x0 - int(win_w / 3)
                w_y0 = c_y0 - 16
                ctypes.windll.user32.SetWindowPos(
                    hwnd, 0, w_x0, w_y0, 0, 0, 0x0001 | 0x0004 | 0x0010
                )

            VK_LBUTTON = 0x01
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            FLAGS = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE

            def _drag_worker(c_x0=c_x0, c_y0=c_y0, w_x0=w_x0, w_y0=w_y0):
                try:
                    SW_MAXIMIZE = 3
                    deadline = time.time() + 30.0  # safety cap
                    while time.time() < deadline:
                        state = ctypes.windll.user32.GetAsyncKeyState(VK_LBUTTON)
                        if not (state & 0x8000):
                            break
                        pt = wintypes.POINT()
                        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                        # Aero-snap: cursor hit the top edge of the monitor
                        # -> maximize and end the drag. Mirrors native
                        # Windows behavior (drag to top = snap maximize).
                        if pt.y <= 0:
                            ctypes.windll.user32.ShowWindow(hwnd, SW_MAXIMIZE)
                            break
                        new_x = w_x0 + (pt.x - c_x0)
                        new_y = w_y0 + (pt.y - c_y0)
                        ctypes.windll.user32.SetWindowPos(
                            hwnd, 0, new_x, new_y, 0, 0, FLAGS
                        )
                        time.sleep(0.008)  # ~120Hz
                except Exception as e:
                    _debug_log(f"drag worker: {e}")

            threading.Thread(target=_drag_worker, daemon=True).start()
            _debug_log(f"_tb_drag started hwnd={hwnd}")
        except Exception as e:
            _debug_log(f"_tb_drag failed: {e}")

    def _tb_pick_import_folder():
        """Native folder-picker for the Import-data flow. Returns the
        absolute path string, or None if the user cancelled. Default
        starting location is the Desktop, since that's where the
        uninstaller writes its exports.
        """
        try:
            import webview as _webview
            start_dir = ""
            try:
                up = os.environ.get("USERPROFILE", "")
                if up:
                    desk = Path(up) / "Desktop"
                    if desk.is_dir():
                        start_dir = str(desk)
            except Exception:
                pass
            result = window.create_file_dialog(
                _webview.FOLDER_DIALOG,
                directory=start_dir,
                allow_multiple=False,
            )
            if not result:
                return None
            if isinstance(result, (list, tuple)):
                return result[0] if result else None
            return str(result)
        except Exception as e:
            _debug_log(f"pick_import_folder failed: {e}")
            return None

    window.expose(
        _tb_minimize, _tb_maximize_toggle, _tb_close, _tb_drag,
        _tb_pick_import_folder,
    )

    def _debug_log(msg):
        """Write a diagnostic line to debug.log next to server.py so we
        can actually see what happened when running windowless. pythonw.exe
        has no stdout/stderr we can read, and pywebview callbacks swallow
        exceptions silently."""
        try:
            with open(BASE_DIR / "debug.log", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    def _find_my_window_hwnd(require_visible=True):
        """Walk every top-level window, filter to ones owned by THIS
        process, return the first with 'YT Grab' in the title.
        require_visible controls whether hidden windows are excluded --
        set to False during the hidden-launch prep since the window
        hasn't been revealed yet.

        More reliable than FindWindowW on the WebView2 backend because
        title matching is substring + process-filtered."""
        import ctypes
        from ctypes import wintypes
        my_pid = os.getpid()
        result = {"hwnd": None}

        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lp):
            try:
                pid = wintypes.DWORD()
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value != my_pid:
                    return True
                if require_visible and not ctypes.windll.user32.IsWindowVisible(hwnd):
                    return True
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                if "YT Grab" in buf.value:
                    result["hwnd"] = hwnd
                    return False  # stop enumeration
            except Exception:
                pass
            return True

        ctypes.windll.user32.EnumWindows(EnumWindowsProc(_cb), 0)
        return result["hwnd"]

    def _prep_and_show():
        """Runs once after webview.start fires. The window was created
        hidden=True -- meaning it exists at the Win32 level but has
        never been displayed. We do all the prep work (icon + target
        size) while the user sees nothing, then call ShowWindow with
        SW_MAXIMIZE which atomically transitions hidden -> visible +
        maximized in a single frame. No flicker, no off-centered flash,
        no 'snap' animation.

        Without the icon-set, Windows reads pythonw.exe's icon for
        taskbar/pin display and the whole thing shows as Python."""
        import ctypes
        _debug_log("_prep_and_show fired, pid=" + str(os.getpid()))

        SW_MAXIMIZE = 3
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        LR_DEFAULTSIZE = 0x00000040

        # Poll up to ~2.5s for the hidden WebView2 window to exist.
        # require_visible=False because we CREATED it hidden -- we're
        # looking at a window that hasn't been shown yet.
        hwnd = None
        for attempt in range(25):
            hwnd = _find_my_window_hwnd(require_visible=False)
            if hwnd:
                _debug_log(f"found hidden hwnd on attempt {attempt}: {hwnd}")
                break
            time.sleep(0.1)
        if not hwnd:
            _debug_log("FAILED to find hwnd after 2.5s of polling")
            # Fallback: try to show via pywebview anyway so the user
            # isn't stuck with an invisible app.
            try: window.show()
            except Exception: pass
            return

        # Set icon FIRST -- still hidden at this point, so the taskbar
        # entry spawns with the correct icon from the very first frame.
        if icon_path and os.path.exists(icon_path):
            try:
                hicon = ctypes.windll.user32.LoadImageW(
                    None, icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE
                )
                if hicon:
                    ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
                    ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
                    ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
                    ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
                    _debug_log(f"WM_SETICON sent 4x (pre-show) with hicon={hicon}")
                else:
                    _debug_log(f"LoadImageW returned 0 for {icon_path}")
            except Exception as e:
                _debug_log(f"WM_SETICON failed: {e}")
        else:
            _debug_log(f"no icon_path or file missing: {icon_path}")

        # Dark title bar via DWM. Windows 10 20H1+ and Windows 11
        # respect DWMWA_USE_IMMERSIVE_DARK_MODE (attribute 20) --
        # swaps the light gray title bar for a dark one that matches
        # our app's dark theme. Massive visual upgrade; older Windows
        # silently ignores. Done while still hidden so the first
        # painted frame already has a dark title bar.
        try:
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            dark_value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(dark_value),
                ctypes.sizeof(dark_value),
            )
            _debug_log("dark title bar applied via DWM")
        except Exception as e:
            _debug_log(f"dark title bar skipped (likely older Windows): {e}")

        # THE MAGIC LINE: SW_MAXIMIZE on a hidden window transitions
        # it from SW_HIDE state to visible + maximized in a single
        # Win32 call. Per MSDN SW_MAXIMIZE "Activates the window and
        # displays it as a maximized window." So the user's FIRST
        # frame of this window is already full-screen maximized.
        try:
            ctypes.windll.user32.ShowWindow(hwnd, SW_MAXIMIZE)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            # Keep pywebview's internal `hidden` flag in sync so any
            # later window.hide/show calls work correctly.
            try: window.show()
            except Exception: pass
            _debug_log("clean maximize-reveal complete")
        except Exception as e:
            _debug_log(f"reveal failed: {e}")

    try:
        # Pin WebView2's user-data dir to %LOCALAPPDATA%\YTGrab\webview
        # so localStorage (theme settings, saved views, etc.) persists
        # reliably across restarts -- and survives renaming or moving
        # the exe. Without an explicit path, WebView2 derives one from
        # the process name, which drifts between `python server.py`
        # and packaged YTGrab.exe builds and effectively amnesia-bombs
        # users on their first packaged launch. private_mode=False is
        # also required; private mode uses an in-memory storage profile
        # that vanishes on exit regardless of storage_path.
        storage_dir = os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
            "YTGrab", "webview"
        )
        try:
            os.makedirs(storage_dir, exist_ok=True)
        except Exception:
            storage_dir = None  # fall back to pywebview default if FS denies us
        # Pass the callback positionally to webview.start() -- this is
        # how Luna does it (and works reliably across pywebview versions).
        webview.start(
            _prep_and_show,
            debug=False,
            private_mode=False,
            storage_path=storage_dir,
        )
    finally:
        os._exit(0)


def _focus_existing_instance():
    """If another YT Grab is running, find its pywebview window
    and bring it to the front. Does NOT open a browser. Returns True
    if we successfully focused an existing window, False otherwise."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        from ctypes import wintypes
        # Find any top-level window titled "YT Grab" -- regardless
        # of which process owns it. Can't filter by PID because we want
        # OTHER processes' windows. The title match is specific enough.
        result = {"hwnd": None}
        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lp):
            try:
                if not ctypes.windll.user32.IsWindowVisible(hwnd):
                    return True
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                if "YT Grab" in buf.value:
                    result["hwnd"] = hwnd
                    return False
            except Exception:
                pass
            return True

        ctypes.windll.user32.EnumWindows(EnumWindowsProc(_cb), 0)
        hwnd = result["hwnd"]
        if not hwnd:
            return False
        # Classic focus-stealing bypass + activate
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x12, 0, 0x0002, 0)
        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        ctypes.windll.user32.BringWindowToTop(hwnd)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    # Second-instance guard: if YT Grab is already running, focus
    # its existing window and exit. If the port is in use but no window
    # can be found, we've got a zombie from a prior close that hasn't
    # released the port yet -- kill it and continue launching fresh.
    # Without this, clicking the pin right after closing = silent fail
    # because the zombie pretends to be a second instance.
    if _port_in_use(PORT):
        # Give any concurrent first-instance a moment to create its window
        # (covers the rapid-double-click case where two pythonw.exe
        # instances launched almost together).
        time.sleep(0.4)
        if _focus_existing_instance():
            sys.exit(0)
        # No window + port held = zombie. Kill anything on port 8765,
        # wait for the socket to release, then fall through to launch.
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-NetTCPConnection -LocalPort {} -State Listen -ErrorAction SilentlyContinue | "
                 "ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}".format(PORT)],
                capture_output=True, timeout=3,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
        except Exception:
            pass
        # Wait up to 2s for the port to actually release
        for _ in range(10):
            if not _port_in_use(PORT):
                break
            time.sleep(0.2)

    # Windows taskbar identity -- must be set BEFORE any window is
    # created. Makes pinned windows show as "YT Grab" with our
    # icon instead of grouping under Chrome.
    _set_app_user_model_id()
    # First-run Start Menu + Desktop shortcut creation. Makes source-run
    # pinning show as "YT Grab" (inherited from the shortcut's
    # filename + icon) instead of falling back to pythonw's metadata.
    _ensure_windows_shortcuts()

    # Brand the console if one's attached (dev mode). No-op in pythonw
    # and frozen console=False builds.
    _brand_console()
    try:
        print("==================================================")
        print("  YT Grab")
        print(f"  http://localhost:{PORT}")
        print(f"  Downloads folder: {DOWNLOADS_DIR}")
        if HAS_FFPROBE:
            print("  ffprobe: OK (metadata + thumbnail embedding enabled)")
        else:
            print("  ffprobe: NOT FOUND -- embedding disabled.")
        print("==================================================")
    except Exception:
        pass

    # Flask in a background thread; pywebview on the main thread.
    # pywebview REQUIRES the main thread on Windows for its WebView2 host.
    threading.Thread(target=_run_flask_bg, daemon=True).start()
    threading.Thread(target=_heartbeat_monitor, daemon=True).start()

    # Give Flask a beat to bind the port before pywebview asks for the URL.
    time.sleep(0.6)

    # Try pywebview first -- this is the premium path (native window,
    # proper AppUserModelID, custom icon). If it fails (WebView2 runtime
    # missing, import error, etc.), fall back to Chrome app-mode and
    # keep Flask running so the app is still usable.
    try:
        _launch_pywebview()
    except Exception as e:
        try:
            print(f"[yt-dl] pywebview unavailable ({e}); falling back to browser")
        except Exception:
            pass
        _fallback_open_browser(f"http://localhost:{PORT}")
        # Keep the Flask thread alive in the fallback path. Normal
        # heartbeat-timeout shutdown applies.
        while True:
            time.sleep(3600)
