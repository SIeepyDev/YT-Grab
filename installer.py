"""
YT Grab - Online Installer / Auto-Updater
==========================================

Tiny stdlib-only Tkinter bootstrapper. Pulls the real app from the
public GitHub repo's latest release. One .exe does three jobs:

  1. First install: downloads YTGrab.exe + YTGrabUninstaller.exe to
     %LOCALAPPDATA%\\Programs\\YTGrab, creates Desktop + Start Menu
     shortcuts, launches the app.
  2. Auto-update on launch: shortcut points at this setup .exe, not
     YTGrab.exe. Every launch re-checks GitHub for a newer release
     and pulls it before opening the app. Offline? Just launches
     what you have.
  3. Manual re-run: same code path as #2, so there is nothing for
     the user to learn.

Build with PyInstaller via Installer.spec ->
    dist\\YTGrabSetup.exe
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

APP_EXE_NAME      = "YTGrab.exe"
UNINST_EXE_NAME   = "YTGrabUninstaller.exe"
SETUP_EXE_NAME    = "YTGrabSetup.exe"
VERSION_FILE_NAME = "version.txt"

# Standard Windows per-user app install location. No admin required.
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")).resolve() / "Programs" / "YTGrab"

USERPROFILE = Path(os.environ.get("USERPROFILE", "")).resolve()
APPDATA     = Path(os.environ.get("APPDATA", "")).resolve()
DESKTOP     = USERPROFILE / "Desktop" if USERPROFILE.name else None
START_MENU  = (APPDATA / "Microsoft" / "Windows" / "Start Menu" / "Programs"
               if APPDATA.name else None)


# --- Worker logic -----------------------------------------------------

class InstallerWorker:
    """Runs install/update steps off the UI thread."""

    def __init__(self, log_cb, progress_cb, done_cb):
        self.log = log_cb
        self.progress = progress_cb
        self.done = done_cb
        self.error: str | None = None

    def run(self):
        try:
            self._step()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        finally:
            self.done(self.error)

    def _step(self):
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)
        is_first_install = not (INSTALL_DIR / APP_EXE_NAME).exists()
        installed_version = self._read_installed_version()

        self.log("Checking for updates...")
        self.progress(0.05)
        latest = self._fetch_latest_release()

        if latest is None:
            # Network or API failure. Launch what we have; only fail if
            # there's nothing installed yet.
            if is_first_install:
                raise RuntimeError(
                    "Can't reach GitHub to download YT Grab. "
                    "Check your internet connection and try again."
                )
            self.log("Offline — launching installed version.")
            self.progress(1.0)
            self._launch_app()
            return

        target_version = latest["tag_name"].lstrip("v")

        if not is_first_install and installed_version == target_version:
            self.log(f"Up to date (v{target_version}). Launching...")
            self.progress(1.0)
            self._launch_app()
            return

        # Need to download.
        self.log(f"Downloading v{target_version}...")
        assets = {a["name"]: a["browser_download_url"]
                  for a in latest.get("assets", [])}

        # Kill any running instance so we can overwrite YTGrab.exe.
        self._kill_running_app()

        self._download_asset(assets, APP_EXE_NAME,    0.10, 0.75)
        self._download_asset(assets, UNINST_EXE_NAME, 0.75, 0.85)

        # Self-copy so the installed setup.exe stays in sync with what
        # the user downloaded. After first install this is a no-op.
        self._self_copy_into_install_dir()
        self.progress(0.90)

        # Persist installed version.
        (INSTALL_DIR / VERSION_FILE_NAME).write_text(
            target_version, encoding="utf-8"
        )

        # v1.9.1: create shortcuts on EVERY install, not just the first.
        # Users reported vanishing Desktop shortcuts (Windows sometimes
        # sweeps orphaned .lnks; users also delete them and then can't
        # find a way to get them back). Re-creating on every launch of
        # the installer is idempotent and cheap.
        self.log("Creating shortcuts...")
        self._create_shortcuts()

        self.progress(0.98)
        self.log(f"Installed v{target_version}. Launching...")
        time.sleep(0.3)
        self.progress(1.0)
        self._launch_app()
        # v1.8.1: clean up the downloaded YTGrabSetup.exe the user
        # double-clicked from their Downloads folder, since we just
        # stashed a canonical copy inside the install folder via
        # _self_copy_into_install_dir(). Without this cleanup the user
        # ends up with two identical setup .exes -- theirs in
        # Downloads, ours in the install folder.
        self._schedule_original_setup_delete()

    # -- helpers ------------------------------------------------------

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

    def _download_asset(self, assets, name, p_start, p_end):
        url = assets.get(name)
        if not url:
            raise RuntimeError(f"Release is missing asset: {name}")
        target = INSTALL_DIR / name
        tmp = target.with_suffix(target.suffix + ".part")
        self.log(f"  {name}")
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
                target.unlink()
            tmp.replace(target)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass

    def _self_copy_into_install_dir(self):
        """Keep a copy of this setup .exe inside the install folder so
        the shortcut has a stable target that always points at the
        latest updater. No-op when already running from there."""
        try:
            if getattr(sys, "frozen", False):
                me = Path(sys.executable).resolve()
            else:
                me = Path(__file__).resolve()
        except Exception:
            return
        target = INSTALL_DIR / SETUP_EXE_NAME
        try:
            if me == target:
                return
            shutil.copy2(me, target)
        except Exception as exc:
            self.log(f"  (self-copy skipped: {exc})")

    def _kill_running_app(self):
        """Best-effort: kill any running YTGrab.exe so we can overwrite
        it. Silently ignores failures (nothing running = fine)."""
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", APP_EXE_NAME],
                capture_output=True, text=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            time.sleep(0.4)
        except Exception:
            pass

    def _create_shortcuts(self):
        """Make Desktop + Start Menu .lnks for the app AND the
        uninstaller. The app shortcut points at SETUP_EXE_NAME so every
        launch triggers an update check; the uninstaller shortcut
        points directly at YTGrabUninstaller.exe."""
        app_target    = INSTALL_DIR / SETUP_EXE_NAME
        uninst_target = INSTALL_DIR / UNINST_EXE_NAME
        icon          = INSTALL_DIR / APP_EXE_NAME   # shared icon
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

    def _schedule_original_setup_delete(self):
        """Queue a deletion of the user-downloaded YTGrabSetup.exe --
        i.e. the copy the user double-clicked from their Downloads
        folder, NOT the canonical copy we just wrote to the install
        directory.

        We can't delete our own running .exe, so we spawn a detached
        hidden PowerShell process that waits a few seconds for us to
        exit, then removes the original file. Same pattern as the
        uninstaller's self-delete.
        """
        if not getattr(sys, "frozen", False):
            return  # running from source -- nothing to clean up
        try:
            me = Path(sys.executable).resolve()
        except Exception:
            return
        installed_copy = (INSTALL_DIR / SETUP_EXE_NAME).resolve()
        if me == installed_copy:
            # Being run from inside the install folder (the shortcut
            # path). Leave it alone -- that IS the canonical copy.
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
            raise RuntimeError("App exe missing after install.")
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
            raise RuntimeError(f"Couldn't launch app: {exc}")


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
            text="Fetching the latest release from GitHub...",
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
