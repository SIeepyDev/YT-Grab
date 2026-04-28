"""
YT Grab - bootstrap helpers (install + self-update).
=====================================================

Imported by server.py at the top of __main__. NOT a separate PyInstaller
target anymore -- as of v1.18 there is a single YTGrab.exe that IS the
app. The code below runs before Flask/pywebview ever start:

  * First run (user double-clicked YTGrab.exe from Downloads):
    - copy ourselves to %LOCALAPPDATA%\\Programs\\YTGrab\\YTGrab.exe
    - fetch YTGrabUninstaller.exe from the latest GitHub release and
      drop it next to us in the install folder
    - create Desktop + Start Menu shortcuts (one for the app, one for
      the uninstaller)
    - spawn the just-installed YTGrab.exe and exit; the new process
      goes straight through bootstrap_or_update() into the real app
    - schedule a deletion of the Downloads-folder original so the user
      doesn't end up with two copies of the same 60 MB binary

  * Subsequent launches (we are INSTALL_DIR/YTGrab.exe):
    - fast GitHub check for a newer tag; if found, download the new
      YTGrab.exe to .new, rename-swap (NTFS lets a running exe be
      renamed), spawn the new one, exit. The new process's
      bootstrap_or_update() handles the .old cleanup on startup.
    - if up to date (or offline / rate-limited), fall through silently
      to the app's normal startup path. Never blocks the user.

  * Running from source / non-Windows / not frozen: returns immediately
    so `python server.py` in dev mode still works unchanged.

The install/update GUI is stdlib tkinter only. The real app's Flask
and pywebview dependencies are NOT imported here so a plain source-mode
launch never pays the Tkinter tax either (tkinter is imported lazily
inside the GUI helpers).
"""

from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import uuid
from pathlib import Path

# winreg is Windows-only stdlib. Imported inside _register_uninstall_entry
# so the module still loads cleanly on the dev side (Linux sandbox /
# macOS) where this file may be syntax-checked but never executed.


# --- Identity --------------------------------------------------------

GITHUB_OWNER = "SIeepyDev"
GITHUB_REPO  = "YT-Grab"
RELEASE_API  = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

EXE_NAME        = "YTGrab.exe"
UNINST_NAME     = "YTGrabUninstaller.exe"
VERSION_FILE    = "version.txt"
INSTALL_ID_FILE = "install_id.txt"


def _read_app_version():
    """Read the canonical app version from the bundled
    .release-please-manifest.json. release-please owns this file --
    every release PR merge bumps it -- so by reading it here we
    guarantee APP_VERSION never drifts from the git tag.

    Source priority:
      * Frozen build:  sys._MEIPASS / .release-please-manifest.json
                       (file bundled into YTGrab.exe by YTGrab.spec)
      * Dev mode:      <repo>/.release-please-manifest.json
      * Fallback:      "0.0.0" (only hit if the manifest was somehow
                       not bundled -- a build bug worth surfacing)

    Replaces the v1.19 hardcoded APP_VERSION constant which had to be
    bumped manually before each release. v1.19.1 shipped with
    APP_VERSION="1.19.1" stuck at the same value through what should
    have been a v1.20.0 release; the registry wrote the stale 1.19.1
    while every other surface read 1.20.0 from the manifest. This
    function closes that gap.
    """
    candidates = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / ".release-please-manifest.json")  # noqa: SLF001
    candidates.append(Path(__file__).resolve().parent / ".release-please-manifest.json")
    for p in candidates:
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            v = data.get(".") or data.get("yt-grab")
            if isinstance(v, str) and v.strip():
                return v.strip()
        except Exception:
            continue
    return "0.0.0"


APP_VERSION = _read_app_version()

# Apps & Features (Add/Remove Programs) registry key. HKCU because we
# install per-user with no admin elevation -- HKLM would require UAC.
# Naming "YTGrab" not "YT Grab" so the registry path is shell-safe.
ARP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\YTGrab"
ARP_DISPLAY_NAME = "YT Grab"
ARP_PUBLISHER    = "Sleepy Productions"

# Per-user install location; no admin required.
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")).resolve() / "Programs" / "YTGrab"

USERPROFILE = Path(os.environ.get("USERPROFILE", "")).resolve()
APPDATA     = Path(os.environ.get("APPDATA", "")).resolve()
DESKTOP     = USERPROFILE / "Desktop" if USERPROFILE.name else None
START_MENU  = (APPDATA / "Microsoft" / "Windows" / "Start Menu" / "Programs"
               if APPDATA.name else None)


# --- Public entry point ---------------------------------------------

def bootstrap_or_update() -> bool:
    """Called once by server.py at the top of __main__.

    Returns True if the current process should continue into the app's
    normal startup. Returns False if this process should exit (install
    completed, or self-update spawned a replacement) -- the caller is
    expected to sys.exit(0) in that case. Errors are swallowed; the
    app launches on our best-effort promise of "never block the user."
    """
    # Only frozen Windows builds participate. Source mode (dev) and any
    # non-Windows environment fall straight through to normal startup.
    if sys.platform != "win32":
        return True
    if not getattr(sys, "frozen", False):
        return True

    try:
        _cleanup_update_leftovers()
        _cleanup_legacy_artifacts()

        running = Path(sys.executable).resolve()
        installed = (INSTALL_DIR / EXE_NAME)
        am_installed = installed.exists() and running == installed.resolve()

        if not am_installed:
            _run_install_flow()
            return False  # the spawned installed process took over

        # We are the installed copy. Check GitHub for a newer release.
        swapped = _maybe_self_update()
        if swapped:
            return False  # the new outer spawned; we exit

        # Up to date or offline: fall through to the real app.
        return True
    except Exception:
        # If anything in bootstrap fails, log to stderr and let the app
        # start. A broken updater should never prevent the app from
        # running.
        traceback.print_exc()
        return True


# --- Leftover cleanup -----------------------------------------------

def _cleanup_update_leftovers():
    """Wipe the .old / .new files a prior self-update run may have
    left behind. Runs every launch so the install folder stays tidy."""
    if not INSTALL_DIR.exists():
        return
    for leftover in (EXE_NAME + ".old", EXE_NAME + ".new"):
        p = INSTALL_DIR / leftover
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass


def _cleanup_legacy_artifacts():
    """Remove files that existed under pre-1.18 layouts but are no
    longer part of an install:
      * YTGrabSetup.exe     -- the pre-1.17 standalone updater.
      * YTGrabApp.exe       -- the v1.17 bundled-wrapper intermediate.
    Best-effort; locked files stay (uninstaller sweeps them later)."""
    for name in ("YTGrabSetup.exe", "YTGrabApp.exe"):
        p = INSTALL_DIR / name
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass


# --- First-run install flow -----------------------------------------

def _run_install_flow():
    """Show the install progress GUI and do the actual install. Blocks
    until the GUI closes. Returns only after the installed copy has
    been spawned (or on unrecoverable error)."""
    import tkinter as tk
    from tkinter import ttk

    state = {"done": False, "error": None}

    def worker(log, progress, finish):
        try:
            _do_install_steps(log, progress)
        except Exception as exc:
            state["error"] = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        finally:
            finish(state["error"])

    root = tk.Tk()
    root.title("YT Grab Setup")
    W, H = 460, 230
    try:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{W}x{H}+{max(0,(sw-W)//2)}+{max(0,(sh-H)//2)}")
    except Exception:
        root.geometry(f"{W}x{H}")
    root.resizable(False, False)
    root.configure(bg="#1a1a1a")

    frm = tk.Frame(root, bg="#1a1a1a", padx=26, pady=22)
    frm.pack(fill="both", expand=True)
    tk.Label(frm, text="YT Grab Setup", bg="#1a1a1a", fg="#e8e8e8",
             font=("Segoe UI Semibold", 15)).pack(anchor="w")
    tk.Label(frm, text="Setting up YT Grab on this PC...",
             bg="#1a1a1a", fg="#9a9a9a",
             font=("Segoe UI", 9), wraplength=400,
             justify="left").pack(anchor="w", pady=(2, 18))

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("YT.Horizontal.TProgressbar",
                    background="#a78bfa", troughcolor="#2a2a2a",
                    bordercolor="#1a1a1a",
                    lightcolor="#a78bfa", darkcolor="#a78bfa")
    bar = ttk.Progressbar(frm, style="YT.Horizontal.TProgressbar",
                          maximum=1.0, length=400, mode="determinate")
    bar.pack(fill="x")

    status_var = tk.StringVar(value="Starting...")
    tk.Label(frm, textvariable=status_var, bg="#1a1a1a", fg="#7a7a7a",
             font=("Consolas", 9), anchor="w",
             wraplength=400, justify="left").pack(
        anchor="w", fill="x", pady=(14, 0))

    def set_status(msg): root.after(0, status_var.set, msg)
    def set_progress(f): root.after(0, lambda: bar.configure(
        value=max(0.0, min(1.0, float(f)))))

    def finish(error):
        def _close():
            if error:
                status_var.set(f"Failed: {error}")
                bar["value"] = 0
                return  # stay open on error so user can read
            status_var.set("Done. Launching YT Grab...")
            root.after(700, root.destroy)
        root.after(0, _close)

    threading.Thread(
        target=worker, args=(set_status, set_progress, finish),
        daemon=True,
    ).start()
    root.mainloop()


def _do_install_steps(log, progress):
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    # A prior install may have the app running. Kill it so we can
    # overwrite the installed YTGrab.exe. Never kills ourselves (PID
    # filter).
    log("Preparing install folder...")
    progress(0.05)
    _kill_other_ytgrab_instances()

    # Copy ourselves into the install folder. Overwrites any previous
    # install's YTGrab.exe.
    log("Copying YT Grab into install folder...")
    progress(0.20)
    _self_copy_into_install_dir()

    # Extract the bundled YTGrabUninstaller.exe out of our own
    # PyInstaller data resources. Used to download from GitHub but
    # that broke pre-release testing -- a v1.19.1 YTGrab.exe would
    # download the v1.18 uninstaller (whatever was on releases/latest)
    # and the v1.19 install code's expectations would fail silently
    # (e.g. v1.19's _unregister_arp_entry would never run because
    # the v1.18 uninstaller didn't have it). Bundling guarantees the
    # uninstaller version always matches the installer's version.
    log("Extracting uninstaller...")
    progress(0.35)
    _extract_bundled_uninstaller()
    progress(0.85)

    log("Creating shortcuts...")
    progress(0.88)
    _create_shortcuts()

    # Persist version; auto-update on later launches uses this.
    # Source priority: APP_VERSION (baked at build time) > whatever
    # GitHub's releases/latest reports. This way a brand-new release
    # registers with its own version even before the GitHub tag/release
    # has been published.
    version = APP_VERSION or _fetch_latest_version_string() or "1.0.0"
    try:
        (INSTALL_DIR / VERSION_FILE).write_text(version, encoding="utf-8")
    except Exception:
        pass

    # Register with Windows "Apps & Features" so users can uninstall
    # via the standard path (Settings → Apps → installed apps).
    # Without this the app looks like malware to anything scanning
    # for unregistered installs (AV, CCleaner, system audits).
    log("Registering with Apps & Features...")
    progress(0.92)
    _register_uninstall_entry(version)
    progress(0.94)

    log("Launching YT Grab...")
    time.sleep(0.2)
    progress(1.0)
    _spawn_installed_copy()

    # Queue the source-copy delete. We can't delete our own running
    # .exe, so a detached hidden PowerShell does it after we exit.
    _schedule_source_delete()


# --- Self-update flow (installed -> newer) --------------------------

def _maybe_self_update() -> bool:
    """If a newer YTGrab.exe exists on GitHub, download it, swap the
    running exe in place, spawn the new copy, and return True. The
    caller should sys.exit(0) when we return True.

    Returns False if up to date or the check failed -- either way the
    caller should fall through to the normal app launch."""
    latest = _fetch_latest_release()
    if latest is None:
        return False
    target = (latest.get("tag_name") or "").lstrip("v")
    installed = _read_installed_version()
    if not target or not installed:
        # If either side is unknown we can't meaningfully compare.
        # Record the target so the next launch has something to diff
        # against, and keep going.
        if target:
            try:
                (INSTALL_DIR / VERSION_FILE).write_text(
                    target, encoding="utf-8")
            except Exception:
                pass
        return False
    if not _is_newer(target, installed):
        return False

    url = None
    for a in latest.get("assets", []):
        if a.get("name") == EXE_NAME:
            url = a.get("browser_download_url")
            break
    if not url:
        return False

    new_path = INSTALL_DIR / (EXE_NAME + ".new")
    try:
        _download_url(url, new_path)
    except Exception:
        try:
            new_path.unlink()
        except Exception:
            pass
        return False

    current = INSTALL_DIR / EXE_NAME
    old_path = INSTALL_DIR / (EXE_NAME + ".old")
    try:
        if old_path.exists():
            try:
                old_path.unlink()
            except Exception:
                pass
        if current.exists():
            current.replace(old_path)
        new_path.replace(current)
    except Exception:
        # Rename chain blew up. Try to undo so the next launch isn't
        # broken, then bail.
        try:
            if old_path.exists() and not current.exists():
                old_path.replace(current)
        except Exception:
            pass
        return False

    try:
        (INSTALL_DIR / VERSION_FILE).write_text(target, encoding="utf-8")
    except Exception:
        pass

    # Refresh the Apps & Features DisplayVersion so Settings shows
    # the post-update version, not the pre-update one. Best-effort.
    try:
        _register_uninstall_entry(target)
    except Exception:
        pass

    try:
        subprocess.Popen(
            [str(current)],
            cwd=str(INSTALL_DIR),
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            ),
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return False
    return True


# --- Helpers --------------------------------------------------------

def _read_installed_version() -> str | None:
    vf = INSTALL_DIR / VERSION_FILE
    if vf.is_file():
        try:
            return vf.read_text(encoding="utf-8").strip().lstrip("v")
        except Exception:
            return None
    return None


def _fetch_latest_release(timeout=8):
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            RELEASE_API,
            headers={
                "User-Agent": "YTGrab-Installer",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _fetch_latest_version_string() -> str | None:
    data = _fetch_latest_release(timeout=6)
    if not data:
        return None
    tag = (data.get("tag_name") or "")
    return tag.lstrip("v") or None


def _is_newer(latest: str, current: str) -> bool:
    def parts(s: str) -> tuple[int, ...]:
        clean = []
        for ch in s:
            if ch.isdigit() or ch == ".":
                clean.append(ch)
            else:
                break
        return tuple(int(p) for p in "".join(clean).split(".") if p.isdigit())
    try:
        return parts(latest) > parts(current)
    except Exception:
        return False


def _extract_bundled_uninstaller():
    """Copy YTGrabUninstaller.exe out of our PyInstaller bundle to
    INSTALL_DIR. Replaces the v1.19 download-from-GitHub path which
    had a chicken-and-egg bug: a brand-new v1.19.1 YTGrab.exe would
    download whatever YTGrabUninstaller.exe was tagged "latest" on
    GitHub at that moment -- and during pre-release testing that's
    the OLD v1.18 uninstaller, which doesn't have the registry-
    cleanup code the new YTGrab.exe expects.

    Bundle source path:
      * Frozen build:  sys._MEIPASS / YTGrabUninstaller.exe
      * Dev mode:      <repo>/dist/YTGrabUninstaller.exe (if you've
                       built it; otherwise this raises and you should
                       run build.bat first)
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        src = Path(sys._MEIPASS) / UNINST_NAME  # noqa: SLF001
    else:
        src = Path(__file__).resolve().parent / "dist" / UNINST_NAME
    if not src.is_file():
        raise RuntimeError(
            f"{UNINST_NAME} missing from bundle. This is a build-side "
            f"packaging error -- YTGrab.spec didn't include it. "
            f"Rebuild with build.bat (which builds Uninstaller.spec "
            f"BEFORE YTGrab.spec so the bundle has it)."
        )
    target = INSTALL_DIR / UNINST_NAME
    try:
        if target.exists():
            try:
                target.unlink()
            except Exception:
                pass
        shutil.copy2(src, target)
    except Exception as exc:
        raise RuntimeError(f"Couldn't extract {UNINST_NAME}: {exc}") from exc


def _download_url(url: str, target: Path, progress_range=(0.0, 1.0),
                  on_progress=None):
    p_start, p_end = progress_range
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            url, headers={"User-Agent": "YTGrab-Installer"},
        )
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            total = int(r.headers.get("Content-Length", 0))
            done = 0
            with open(tmp, "wb") as out:
                while True:
                    chunk = r.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if total and on_progress:
                        frac = p_start + (p_end - p_start) * (done / total)
                        on_progress(min(frac, p_end))
        if target.exists():
            try:
                target.unlink()
            except Exception:
                pass
        tmp.replace(target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def _get_or_create_install_id():
    """Stable per-install UUID. Persisted at INSTALL_DIR/install_id.txt
    so it survives auto-updates (we write/read the install dir on every
    update, but only create the UUID once on first install). Future
    versions read this back to detect upgrade-vs-fresh-install. Always
    returns a string; generates one on first call if the file is missing."""
    p = INSTALL_DIR / INSTALL_ID_FILE
    if p.is_file():
        try:
            existing = p.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except Exception:
            pass
    new_id = str(uuid.uuid4())
    try:
        p.write_text(new_id, encoding="utf-8")
    except Exception:
        pass
    return new_id


def _install_dir_size_kb():
    """Walk INSTALL_DIR and sum file sizes in KB. Used for the
    EstimatedSize value in the Apps & Features registration. The
    number is informational only -- Windows doesn't enforce or
    auto-update it -- so a one-time snapshot at install time is fine."""
    total = 0
    try:
        for root, _dirs, files in os.walk(INSTALL_DIR):
            for fn in files:
                try:
                    total += (Path(root) / fn).stat().st_size
                except Exception:
                    pass
    except Exception:
        pass
    return max(1, total // 1024)


def _register_uninstall_entry(version):
    """Write the standard Apps & Features registry keys under HKCU
    so the user can find + uninstall via Settings → Apps → installed
    apps (instead of being limited to our Desktop/Start Menu shortcut).

    HKCU not HKLM: per-user install, no UAC prompt at install time.
    HKLM would require admin elevation we don't ask for.

    Includes a stable install_id (UUID) so future versions can tell
    upgrade-from-1.19.1 vs fresh-install. Without an ID, an upgrade
    looks identical to a fresh install in terms of registry state.
    """
    if sys.platform != "win32":
        return
    try:
        import winreg
    except ImportError:
        return

    install_id = _get_or_create_install_id()
    install_loc = str(INSTALL_DIR)
    uninst_str  = str(INSTALL_DIR / UNINST_NAME)
    icon_str    = f"{INSTALL_DIR / EXE_NAME},0"
    size_kb     = _install_dir_size_kb()

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            ARP_KEY,
            0,
            winreg.KEY_WRITE,
        ) as k:
            winreg.SetValueEx(k, "DisplayName",     0, winreg.REG_SZ, ARP_DISPLAY_NAME)
            winreg.SetValueEx(k, "DisplayVersion",  0, winreg.REG_SZ, str(version))
            winreg.SetValueEx(k, "Publisher",       0, winreg.REG_SZ, ARP_PUBLISHER)
            winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, install_loc)
            winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, uninst_str)
            winreg.SetValueEx(k, "DisplayIcon",     0, winreg.REG_SZ, icon_str)
            winreg.SetValueEx(k, "InstallID",       0, winreg.REG_SZ, install_id)
            winreg.SetValueEx(k, "URLInfoAbout",    0, winreg.REG_SZ,
                              "https://github.com/SIeepyDev/YT-Grab")
            winreg.SetValueEx(k, "EstimatedSize",   0, winreg.REG_DWORD, int(size_kb))
            winreg.SetValueEx(k, "NoModify",        0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "NoRepair",        0, winreg.REG_DWORD, 1)
    except Exception:
        # Registry failure is non-fatal -- the install proceeds, the
        # app works, the user just won't see it in Apps & Features
        # (they still have the shortcut).
        pass


def _self_copy_into_install_dir():
    """Copy the running .exe (from Downloads or wherever) into
    INSTALL_DIR as YTGrab.exe. Overwrite-safe; killed any running
    installed instance just before this."""
    me = Path(sys.executable).resolve()
    target = INSTALL_DIR / EXE_NAME
    if me == target:
        return
    if target.exists():
        try:
            target.unlink()
        except Exception:
            pass
    shutil.copy2(me, target)


def _kill_other_ytgrab_instances():
    """Kill any running YTGrab.exe EXCEPT ourselves. Also sweeps legacy
    names so a 1.17.x machine upgrading via double-click cleans up."""
    my_pid = os.getpid()
    targets = [
        (EXE_NAME,              f"PID ne {my_pid}"),
        ("YTGrabApp.exe",       None),   # v1.17 intermediate
        ("YTGrabSetup.exe",     None),   # pre-1.17 standalone updater
    ]
    for image, fltr in targets:
        cmd = ["taskkill", "/F", "/IM", image]
        if fltr:
            cmd += ["/FI", fltr]
        try:
            subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            pass
    time.sleep(0.4)


def _create_shortcuts():
    app_target    = INSTALL_DIR / EXE_NAME
    uninst_target = INSTALL_DIR / UNINST_NAME
    icon = app_target
    for folder in (DESKTOP, START_MENU):
        if folder is None:
            continue
        try:
            folder.mkdir(parents=True, exist_ok=True)
            _make_shortcut(folder / "YT Grab.lnk",
                           app_target, icon, INSTALL_DIR,
                           description="YT Grab - YouTube downloader")
            _make_shortcut(folder / "Uninstall YT Grab.lnk",
                           uninst_target, icon, INSTALL_DIR,
                           description="Remove YT Grab from this PC")
        except Exception:
            pass


def _make_shortcut(lnk_path, target, icon, working_dir, description):
    desc_ps = description.replace("'", "''")
    ps = (
        "$WS = New-Object -ComObject WScript.Shell; "
        f"$S = $WS.CreateShortcut('{str(lnk_path)}'); "
        f"$S.TargetPath = '{str(target)}'; "
        f"$S.WorkingDirectory = '{str(working_dir)}'; "
        f"$S.IconLocation = '{str(icon)},0'; "
        f"$S.Description = '{desc_ps}'; "
        "$S.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive",
         "-WindowStyle", "Hidden", "-Command", ps],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _spawn_installed_copy():
    target = INSTALL_DIR / EXE_NAME
    if not target.exists():
        raise RuntimeError(
            f"{EXE_NAME} missing after install. Self-copy failed.")
    try:
        subprocess.Popen(
            [str(target)],
            cwd=str(INSTALL_DIR),
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            ),
            close_fds=True,
        )
    except Exception as exc:
        raise RuntimeError(f"Couldn't launch installed copy: {exc}") from exc


def _schedule_source_delete():
    """After we exit, sweep the Downloads copy of YTGrab.exe (the one
    the user double-clicked). Canonical copy inside INSTALL_DIR is
    never touched."""
    me = Path(sys.executable).resolve()
    installed = (INSTALL_DIR / EXE_NAME).resolve()
    if me == installed:
        return
    target_ps = str(me).replace("'", "''")
    ps_script = (
        "$ErrorActionPreference = 'SilentlyContinue'; "
        f"$t = '{target_ps}'; "
        "Start-Sleep -Seconds 3; "
        "for ($i = 1; $i -le 6; $i++) { "
        "  Remove-Item -Path $t -Force -ErrorAction SilentlyContinue; "
        "  if (-not (Test-Path $t)) { break } "
        "  Start-Sleep -Milliseconds 700 "
        "}"
    )
    try:
        subprocess.Popen(
            [
                "powershell", "-NoProfile", "-NonInteractive",
                "-WindowStyle", "Hidden",
                "-ExecutionPolicy", "Bypass",
                "-Command", ps_script,
            ],
            cwd=(os.environ.get("SystemDrive", "C:") + "\\"),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
