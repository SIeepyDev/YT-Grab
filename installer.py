"""
YT Grab - Single-file Installer / Auto-updater
===============================================

This is the one .exe friends download from the public GitHub release
(SIeepyDev/YT-Grab). It bundles two inner binaries as PyInstaller
data resources:

    YTGrabApp.exe          -- the real Flask + pywebview app
    YTGrabUninstaller.exe  -- standalone tkinter uninstaller

On launch it does ONE of three things depending on where it is
running from:

  1. Fresh download (anywhere outside the install folder, e.g. the
     user's Downloads). First-install path: extract both inner
     binaries into %LOCALAPPDATA%\\Programs\\YTGrab, copy self to
     that folder, create Desktop + Start Menu shortcuts (one for
     the app, one for the uninstaller), launch the app, and
     schedule a self-delete of the downloaded copy so the user
     doesn't end up with two identical .exes.

  2. Installed launch, up to date. Runs from the install folder
     (the shortcut target). Verifies the inner binaries are still
     on disk (extracting them again if a user deleted them), then
     launches YTGrabApp.exe.

  3. Installed launch, newer version available. Same as #2 but
     first downloads the new YTGrab.exe from the latest GitHub
     release, renames itself to YTGrab.exe.old (NTFS lets a
     running exe be renamed but not deleted), swaps the new
     binary into place, and re-launches. The new outer exe cleans
     up the .old leftover on its next startup.

Build with PyInstaller via Installer.spec ->
    dist\\YTGrab.exe
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

import tkinter as tk
from tkinter import ttk


# --- Identity ---------------------------------------------------------

GITHUB_OWNER = "SIeepyDev"
GITHUB_REPO  = "YT-Grab"     # the PUBLIC distribution repo
RELEASE_API  = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

# The one asset on each release. Must match Installer.spec's `name=`.
OUTER_EXE_NAME    = "YTGrab.exe"

# Inner binaries extracted from the PyInstaller bundle on install.
APP_EXE_NAME      = "YTGrabApp.exe"
UNINST_EXE_NAME   = "YTGrabUninstaller.exe"

VERSION_FILE_NAME = "version.txt"

# Standard Windows per-user app install location. No admin required.
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")).resolve() / "Programs" / "YTGrab"

USERPROFILE = Path(os.environ.get("USERPROFILE", "")).resolve()
APPDATA     = Path(os.environ.get("APPDATA", "")).resolve()
DESKTOP     = USERPROFILE / "Desktop" if USERPROFILE.name else None
START_MENU  = (APPDATA / "Microsoft" / "Windows" / "Start Menu" / "Programs"
               if APPDATA.name else None)


# --- Helpers (free functions) ----------------------------------------

def _running_exe_path() -> Path:
    """Path of the currently running .exe (or .py during dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(__file__).resolve()


def _bundle_root() -> Path:
    """Directory PyInstaller extracted our bundled data files to.
    Outside PyInstaller this falls back to the repo root so running
    from source stays functional for dev."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # noqa: SLF001
    return Path(__file__).resolve().parent


def _is_newer_version(latest: str, current: str) -> bool:
    """Semver-ish numeric compare. Returns True if `latest` > `current`.
    Non-numeric pre-release suffixes ('-rc1', '-beta') are treated as
    equal to their base version so a release like 1.17.0-rc1 doesn't
    trigger an auto-update over 1.17.0 stable."""
    def _parts(s: str) -> tuple[int, ...]:
        # Strip anything past the first non-numeric/non-dot char so
        # '1.17.0-rc1' -> '1.17.0'.
        clean = []
        for ch in s:
            if ch.isdigit() or ch == ".":
                clean.append(ch)
            else:
                break
        parts = [int(p) for p in "".join(clean).split(".") if p.isdigit()]
        return tuple(parts)
    try:
        return _parts(latest) > _parts(current)
    except Exception:
        return False


# --- Worker logic -----------------------------------------------------

class InstallerWorker:
    """Runs install/update steps off the UI thread."""

    def __init__(self, log_cb, progress_cb, done_cb):
        self.log = log_cb
        self.progress = progress_cb
        self.done = done_cb
        self.error: str | None = None

    # -- entry point --------------------------------------------------

    def run(self):
        try:
            self._cleanup_previous_update_leftover()

            running = _running_exe_path()
            installed = (INSTALL_DIR / OUTER_EXE_NAME).resolve()
            am_installed = (running == installed)

            if not am_installed:
                self._install_from_bundle()
            else:
                self._run_installed()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        finally:
            self.done(self.error)

    # -- first-install path ------------------------------------------

    def _install_from_bundle(self):
        """Fresh download flow. Extract inner bins, copy self into
        install dir, create shortcuts, launch app, clean up source."""
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)

        # If the user re-runs this after an existing install, kill any
        # running YTGrabApp so we can overwrite the inner binary.
        self.log("Preparing install folder...")
        self.progress(0.05)
        self._kill_running_app()

        self.log(f"Extracting {APP_EXE_NAME}...")
        self.progress(0.15)
        self._extract_bundled(APP_EXE_NAME, INSTALL_DIR / APP_EXE_NAME)
        self.progress(0.55)

        self.log(f"Extracting {UNINST_EXE_NAME}...")
        self._extract_bundled(UNINST_EXE_NAME, INSTALL_DIR / UNINST_EXE_NAME)
        self.progress(0.70)

        self.log("Copying installer...")
        self._self_copy_into_install_dir()
        self.progress(0.80)

        # One-time 1.16 -> 1.17 migration courtesy: the old 3-asset
        # architecture shipped a standalone YTGrabSetup.exe next to
        # YTGrab.exe. Leave no orphans behind.
        self._cleanup_legacy_artifacts()

        # Persist install-time version. Used by _run_installed() to
        # decide whether an update is needed on subsequent launches.
        version = _embedded_version_guess()
        if version:
            try:
                (INSTALL_DIR / VERSION_FILE_NAME).write_text(
                    version, encoding="utf-8"
                )
            except Exception:
                pass

        # Shortcut creation is idempotent -- rebuilding on every
        # install fixes the "users reported vanishing Desktop
        # shortcuts" issue from v1.9.1.
        self.log("Creating shortcuts...")
        self._create_shortcuts()
        self.progress(0.95)

        self.log("Launching YT Grab...")
        time.sleep(0.2)
        self.progress(1.0)
        self._launch_app()

        # v1.8.1 holdover: clean up the Downloads-folder copy the user
        # just double-clicked, since the canonical copy now lives in
        # INSTALL_DIR. Without this the user ends up with two identical
        # .exes; confusing and wasteful.
        self._schedule_source_delete()

    # -- installed-launch path ---------------------------------------

    def _run_installed(self):
        """Runs every time the user clicks the installed YTGrab.exe
        shortcut. Does a best-effort update check, then launches the
        inner app."""
        # Defensive: user (or a disk cleanup tool) may have deleted the
        # inner binaries. Re-extract from our bundle so the app still
        # launches.
        app_exe = INSTALL_DIR / APP_EXE_NAME
        if not app_exe.exists():
            self.log(f"Restoring {APP_EXE_NAME}...")
            self.progress(0.10)
            self._extract_bundled(APP_EXE_NAME, app_exe)
        uninst_exe = INSTALL_DIR / UNINST_EXE_NAME
        if not uninst_exe.exists():
            self.log(f"Restoring {UNINST_EXE_NAME}...")
            self._extract_bundled(UNINST_EXE_NAME, uninst_exe)

        self.log("Checking for updates...")
        self.progress(0.25)
        latest = self._fetch_latest_release()

        if latest is None:
            # Offline / API error / rate-limited. Don't block the user.
            self.log("Offline -- launching installed version.")
            self.progress(1.0)
            self._launch_app()
            return

        target_version = latest.get("tag_name", "").lstrip("v")
        current_version = self._read_installed_version()

        if (target_version and current_version
                and _is_newer_version(target_version, current_version)):
            self._perform_self_update(latest, target_version)
            return  # self-update spawns the new outer exe and exits

        # Up to date (or we can't tell -- fall through to launch).
        if target_version:
            self.log(f"Up to date (v{target_version}). Launching...")
        else:
            self.log("Launching...")
        self.progress(1.0)
        self._launch_app()

    # -- self-update (outer shell replacement) -----------------------

    def _perform_self_update(self, latest: dict, target_version: str):
        assets = {a.get("name"): a.get("browser_download_url")
                  for a in latest.get("assets", [])}
        url = assets.get(OUTER_EXE_NAME)
        if not url:
            # Newer release exists but doesn't have the asset we expect.
            # Fail safe: log and launch the installed copy.
            self.log(f"Update skipped (no {OUTER_EXE_NAME} asset). Launching...")
            self.progress(1.0)
            self._launch_app()
            return

        self.log(f"Downloading v{target_version}...")
        new_path = INSTALL_DIR / (OUTER_EXE_NAME + ".new")
        try:
            self._download_url(url, new_path, 0.30, 0.85)
        except Exception as exc:
            # Network issue partway through. Clean up partial file and
            # fall back to launching the current installed version.
            try:
                new_path.unlink()
            except Exception:
                pass
            self.log(f"Update download failed ({exc}). Launching current version.")
            self.progress(1.0)
            self._launch_app()
            return

        self.log("Installing update...")
        self.progress(0.90)
        # NTFS lets a running .exe be RENAMED but not deleted. We rename
        # ourselves aside, move the new binary into our old slot, and
        # spawn it. The new outer exe's _cleanup_previous_update_leftover
        # will remove the .old file on its first launch.
        current = INSTALL_DIR / OUTER_EXE_NAME
        current_old = INSTALL_DIR / (OUTER_EXE_NAME + ".old")
        try:
            if current_old.exists():
                try:
                    current_old.unlink()
                except Exception:
                    pass
            if current.exists():
                current.replace(current_old)
            new_path.replace(current)
        except Exception as exc:
            # Rename chain failed -- restore and launch what we have.
            try:
                if current_old.exists() and not current.exists():
                    current_old.replace(current)
            except Exception:
                pass
            self.log(f"Swap failed ({exc}). Launching current version.")
            self.progress(1.0)
            self._launch_app()
            return

        # Bump the recorded installed version so the next launch
        # doesn't loop-detect the "new" binary as needing yet another
        # update.
        try:
            (INSTALL_DIR / VERSION_FILE_NAME).write_text(
                target_version, encoding="utf-8"
            )
        except Exception:
            pass

        # Force the new outer to re-extract its inner bins by removing
        # the stale ones. _run_installed's "restore if missing" branch
        # will rehydrate them from the freshly-swapped bundle.
        self._kill_running_app()
        for inner in (APP_EXE_NAME, UNINST_EXE_NAME):
            p = INSTALL_DIR / inner
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

        # Spawn the new outer exe. It will re-extract its (fresh)
        # inner bins and launch the app. We exit ourselves so Windows
        # releases the lock on the renamed .old file.
        self.log(f"Relaunching v{target_version}...")
        self.progress(1.0)
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
        except Exception as exc:
            self.log(f"Relaunch failed: {exc}")

    # -- helpers ------------------------------------------------------

    def _cleanup_legacy_artifacts(self):
        """Remove files that only existed under the pre-1.17 layout.
        Best-effort; locked files are left alone and the uninstaller
        will sweep them later if they linger."""
        for name in ("YTGrabSetup.exe",):
            p = INSTALL_DIR / name
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

    def _cleanup_previous_update_leftover(self):
        """Delete YTGrab.exe.old if present. Runs once per launch,
        before anything else, so a just-updated install stops carrying
        its previous binary around."""
        leftover = INSTALL_DIR / (OUTER_EXE_NAME + ".old")
        if leftover.exists():
            try:
                leftover.unlink()
            except Exception:
                pass
        # Also clean up any half-finished .new download from an
        # interrupted previous update attempt.
        partial = INSTALL_DIR / (OUTER_EXE_NAME + ".new")
        if partial.exists():
            try:
                partial.unlink()
            except Exception:
                pass

    def _read_installed_version(self):
        vf = INSTALL_DIR / VERSION_FILE_NAME
        if vf.is_file():
            try:
                return vf.read_text(encoding="utf-8").strip().lstrip("v")
            except Exception:
                return None
        return None

    def _fetch_latest_release(self, timeout=8):
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
        except Exception as exc:
            self.log(f"Network issue: {exc}")
            return None

    def _extract_bundled(self, name: str, dest: Path):
        """Copy a bundled inner binary out of sys._MEIPASS to dest.
        Fails loudly if the bundle is missing the resource -- that
        means a bad build, not a user error."""
        src = _bundle_root() / name
        if not src.is_file():
            raise RuntimeError(
                f"Bundle is missing {name}. Rebuild with build.bat."
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        # copy2 preserves metadata; also overwrite-safe.
        try:
            if dest.exists():
                try:
                    dest.unlink()
                except Exception:
                    # Running inner app lock? best-effort kill already
                    # happened earlier; just let copy2 raise.
                    pass
            shutil.copy2(src, dest)
        except Exception as exc:
            raise RuntimeError(f"Couldn't extract {name}: {exc}") from exc

    def _download_url(self, url: str, target: Path, p_start: float, p_end: float):
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
                        if total:
                            frac = p_start + (p_end - p_start) * (done / total)
                            self.progress(min(frac, p_end))
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

    def _self_copy_into_install_dir(self):
        """Copy this running .exe into INSTALL_DIR so the shortcut has
        a stable target that always points at the latest updater. No-op
        when we are already the installed copy (defensive; the caller
        already routed us through _install_from_bundle because we
        weren't)."""
        try:
            me = _running_exe_path()
        except Exception:
            return
        target = INSTALL_DIR / OUTER_EXE_NAME
        try:
            if me == target:
                return
            if target.exists():
                # Rare: reinstall while a prior outer exe is running
                # from INSTALL_DIR. We can't delete it, but copy2
                # treats the destination as write-mode, which may
                # succeed if nothing has it open. Best-effort; swallow
                # and continue.
                try:
                    target.unlink()
                except Exception:
                    pass
            shutil.copy2(me, target)
        except Exception as exc:
            self.log(f"  (self-copy skipped: {exc})")

    def _kill_running_app(self):
        """Best-effort: kill anything running that would hold a lock on
        files we're about to overwrite. Silently ignores failures.

        Specifically kills:
          - YTGrabApp.exe              (the inner app; never ourselves)
          - YTGrab.exe  *except self*  (any older outer shell instance;
                                        PID filter keeps us alive)
          - YTGrabSetup.exe            (legacy 1.16 updater -- harmless
                                        if not present)
        """
        my_pid = os.getpid()
        targets = [
            (APP_EXE_NAME,      None),
            (OUTER_EXE_NAME,    f"PID ne {my_pid}"),
            ("YTGrabSetup.exe", None),
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

    def _create_shortcuts(self):
        """Make Desktop + Start Menu .lnks for the app AND the
        uninstaller. The app shortcut points at OUTER_EXE_NAME so every
        launch triggers an update check; the uninstaller shortcut
        points directly at YTGrabUninstaller.exe."""
        app_target    = INSTALL_DIR / OUTER_EXE_NAME
        uninst_target = INSTALL_DIR / UNINST_EXE_NAME
        # The outer shell's PyInstaller-embedded icon IS icon.ico, but
        # once installed we don't ship icon.ico as a loose file. Point
        # the .lnk icon at the outer exe itself (index 0 = its embedded
        # icon resource). Works for both shortcuts for visual consistency.
        icon = app_target
        for folder in (DESKTOP, START_MENU):
            if folder is None:
                continue
            try:
                folder.mkdir(parents=True, exist_ok=True)
                self._make_shortcut(
                    folder / "YT Grab.lnk", app_target, icon, INSTALL_DIR,
                    description="YT Grab - YouTube downloader",
                )
                self._make_shortcut(
                    folder / "Uninstall YT Grab.lnk", uninst_target,
                    icon, INSTALL_DIR,
                    description="Remove YT Grab from this PC",
                )
            except Exception as exc:
                self.log(f"  shortcut failed in {folder.name}: {exc}")

    def _make_shortcut(self, lnk_path, target, icon, working_dir,
                       description="YT Grab - YouTube downloader"):
        """Create a .lnk via PowerShell + WScript.Shell COM. No
        non-stdlib deps -- PowerShell ships with every Windows."""
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

    def _schedule_source_delete(self):
        """Queue a deletion of the user-downloaded YTGrab.exe -- the
        copy the user double-clicked from Downloads, NOT the canonical
        copy inside the install folder.

        We can't delete our own running .exe, so we spawn a detached
        hidden PowerShell that waits a few seconds for us to exit and
        then removes the original file. Same pattern as the
        uninstaller's self-delete."""
        if not getattr(sys, "frozen", False):
            return  # running from source -- nothing to clean up
        try:
            me = _running_exe_path()
        except Exception:
            return
        installed_copy = (INSTALL_DIR / OUTER_EXE_NAME).resolve()
        if me == installed_copy:
            # Being run from inside the install folder. Canonical copy.
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

    def _launch_app(self):
        app = INSTALL_DIR / APP_EXE_NAME
        if not app.exists():
            raise RuntimeError(
                f"{APP_EXE_NAME} missing after install. Bundle extraction "
                f"failed -- rebuild with build.bat."
            )
        try:
            subprocess.Popen(
                [str(app)],
                cwd=str(INSTALL_DIR),
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "DETACHED_PROCESS", 0)
                ),
                close_fds=True,
            )
        except Exception as exc:
            raise RuntimeError(f"Couldn't launch app: {exc}") from exc


def _embedded_version_guess() -> str | None:
    """Read the version bundled with this build. We don't bake a string
    constant in at PyInstaller time (to avoid every ship requiring an
    edit of this file); instead we look at the latest GitHub release at
    install time. If that fails we fall back to None and auto-update
    will still heal it on the next successful network call."""
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            RELEASE_API,
            headers={
                "User-Agent": "YTGrab-Installer",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
            data = json.loads(r.read().decode("utf-8"))
            tag = data.get("tag_name", "")
            if tag:
                return tag.lstrip("v")
    except Exception:
        pass
    return None


# --- GUI --------------------------------------------------------------

BG       = "#1a1a1a"
FG       = "#e8e8e8"
FG_MUTED = "#9a9a9a"
ACCENT   = "#a78bfa"     # YT Grab purple
LOG_FG   = "#7a7a7a"


class InstallerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("YT Grab Setup")
        W, H = 460, 230
        # Center on the primary monitor (not just top-left tk default).
        try:
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            x = max(0, (sw - W) // 2)
            y = max(0, (sh - H) // 2)
            root.geometry(f"{W}x{H}+{x}+{y}")
        except Exception:  # noqa: BLE001
            root.geometry(f"{W}x{H}")
        root.resizable(False, False)
        root.configure(bg=BG)

        outer = tk.Frame(root, bg=BG, padx=26, pady=22)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer, text="YT Grab Setup",
            bg=BG, fg=FG,
            font=("Segoe UI Semibold", 15),
        ).pack(anchor="w")

        tk.Label(
            outer,
            text="Setting up YT Grab on this PC...",
            bg=BG, fg=FG_MUTED,
            font=("Segoe UI", 9),
            wraplength=400, justify="left",
        ).pack(anchor="w", pady=(2, 18))

        # Accent-colored progress bar. ttk needs the clam theme to let
        # us recolor the trough.
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "YT.Horizontal.TProgressbar",
            background=ACCENT, troughcolor="#2a2a2a",
            bordercolor=BG, lightcolor=ACCENT, darkcolor=ACCENT,
        )
        self.progress = ttk.Progressbar(
            outer, style="YT.Horizontal.TProgressbar",
            maximum=1.0, length=400, mode="determinate",
        )
        self.progress.pack(fill="x")

        self.status_var = tk.StringVar(value="Starting...")
        tk.Label(
            outer, textvariable=self.status_var,
            bg=BG, fg=LOG_FG,
            font=("Consolas", 9),
            anchor="w", justify="left",
            wraplength=400,
        ).pack(anchor="w", fill="x", pady=(14, 0))

        self.worker_thread: threading.Thread | None = None

    def start(self):
        worker = InstallerWorker(
            log_cb=self._log,
            progress_cb=self._set_progress,
            done_cb=self._on_done,
        )
        self.worker_thread = threading.Thread(
            target=worker.run, daemon=True,
        )
        self.worker_thread.start()

    def _log(self, msg: str):
        self.root.after(0, self.status_var.set, msg)

    def _set_progress(self, frac: float):
        self.root.after(0, self._do_set_progress, float(frac))

    def _do_set_progress(self, frac: float):
        self.progress["value"] = max(0.0, min(1.0, frac))

    def _on_done(self, error):
        self.root.after(0, self._do_done, error)

    def _do_done(self, error):
        if error:
            self.status_var.set(f"Failed: {error}")
            self.progress["value"] = 0
            # Stay open on error so the user can read the message.
            return
        self.status_var.set("Done. Launching YT Grab...")
        self.root.after(800, self.root.destroy)


# --- Entrypoint -------------------------------------------------------

def main():
    if sys.platform != "win32":
        print("YT Grab Setup is Windows-only.", file=sys.stderr)
        sys.exit(1)
    root = tk.Tk()
    app = InstallerApp(root)
    app.start()
    root.mainloop()


if __name__ == "__main__":
    main()
