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
from pathlib import Path


# --- Identity --------------------------------------------------------

GITHUB_OWNER = "SIeepyDev"
GITHUB_REPO  = "YT-Grab"
RELEASE_API  = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

EXE_NAME        = "YTGrab.exe"
UNINST_NAME     = "YTGrabUninstaller.exe"
VERSION_FILE    = "version.txt"

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

    # Download the uninstaller from the GitHub release. If GitHub is
    # unreachable, surface it -- the uninstaller IS part of a complete
    # install, and the user can retry once they're back online.
    log("Fetching YTGrabUninstaller.exe...")
    progress(0.35)
    _download_uninstaller_from_github(progress_range=(0.35, 0.85))

    log("Creating shortcuts...")
    progress(0.88)
    _create_shortcuts()

    # Persist version; auto-update on later launches uses this.
    version = _fetch_latest_version_string()
    if version:
        try:
            (INSTALL_DIR / VERSION_FILE).write_text(version, encoding="utf-8")
        except Exception:
            pass
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


def _download_uninstaller_from_github(progress_range=(0.0, 1.0)):
    latest = _fetch_latest_release()
    if latest is None:
        raise RuntimeError(
            "Can't reach GitHub to download the uninstaller. "
            "Check your internet connection and re-run YTGrab.exe."
        )
    url = None
    for a in latest.get("assets", []):
        if a.get("name") == UNINST_NAME:
            url = a.get("browser_download_url")
            break
    if not url:
        raise RuntimeError(
            f"Release is missing asset: {UNINST_NAME}. This is a "
            f"packaging error -- please report it."
        )
    _download_url(url, INSTALL_DIR / UNINST_NAME, progress_range)


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
