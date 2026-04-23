"""
YT Grab - Standalone Uninstaller
=================================

A tkinter GUI uninstaller. Single big button -- click and it does
everything: optional export, kill app, close Explorer on install folder,
wipe app data, remove shortcuts, wipe webview user-data, self-delete the
install folder.

Build with PyInstaller via Uninstaller.spec ->
    dist\\YTGrabUninstaller.exe
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk


# --- Paths ------------------------------------------------------------

def _install_dir() -> Path:
    """Folder containing this uninstaller.

    When frozen by PyInstaller, sys.executable is the .exe itself; its
    parent is the install folder we want to wipe. When run from source
    during dev, fall back to the script's parent.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


INSTALL_DIR = _install_dir()
USERPROFILE = Path(os.environ.get("USERPROFILE", "")).resolve()
APPDATA = Path(os.environ.get("APPDATA", "")).resolve()
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", "")).resolve()
TEMP_DIR = Path(os.environ.get("TEMP", r"C:\Windows\Temp"))

DESKTOP = USERPROFILE / "Desktop" if USERPROFILE else None
START_MENU = (
    APPDATA / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    if APPDATA
    else None
)

# Additional app-data locations the app creates at runtime. Wiping
# these is part of "remove everything" so no stale WebView2 cache,
# localStorage, or tmp extracts survive the uninstall.
WEBVIEW_DIR = LOCALAPPDATA / "YTGrab" if LOCALAPPDATA.name else None

SHORTCUTS = []
if DESKTOP:
    SHORTCUTS += [
        DESKTOP / "YT Grab.lnk",
        DESKTOP / "Uninstall YT Grab.lnk",   # v1.9.1
        DESKTOP / "YT Downloader.lnk",       # legacy
    ]
if START_MENU:
    SHORTCUTS += [
        START_MENU / "YT Grab.lnk",
        START_MENU / "Uninstall YT Grab.lnk",   # v1.9.1
        START_MENU / "YT Downloader.lnk",       # legacy
    ]


# --- Worker logic -----------------------------------------------------

# Worker phases -> progress fractions. Keeps the progress bar monotonic
# and gives each step a stable slice of the bar.
P_STARTED  = 0.02
P_EXPORT   = 0.25
P_KILL     = 0.35
P_CLOSE_EX = 0.55
P_LINKS    = 0.65
P_WEBVIEW  = 0.80
P_SCHEDULE = 0.95
P_DONE     = 1.00


class UninstallerWorker:
    """Runs the actual uninstall steps off the UI thread."""

    def __init__(self, status_cb, progress_cb, done_cb, export_first: bool):
        self.status = status_cb
        self.progress = progress_cb
        self.done = done_cb
        self.export_first = export_first
        self.export_target: Path | None = None
        self.had_error = False

    def run(self):
        try:
            self.progress(P_STARTED)
            if self.export_first:
                self._export_data()
            self.progress(P_EXPORT)

            self._kill_process()
            self.progress(P_KILL)

            self._close_explorer_windows()
            self.progress(P_CLOSE_EX)

            self._remove_shortcuts()
            self.progress(P_LINKS)

            self._wipe_webview_cache()
            self.progress(P_WEBVIEW)

            # The install folder IS the "app data" folder in the
            # bootstrapper-based install, so a single self-delete of
            # INSTALL_DIR wipes downloads/, previous_downloads/,
            # debug.log, version.txt and the three .exes in one shot.
            self._schedule_self_delete()
            self.progress(P_SCHEDULE)
        except Exception as exc:  # noqa: BLE001
            self.had_error = True
            self.status(f"Error: {exc}")
        finally:
            self.progress(P_DONE)
            self.done(self.had_error, self.export_target)

    # -- steps ---------------------------------------------------------

    def _export_data(self):
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        target = (DESKTOP or INSTALL_DIR) / f"YTGrab-export-{ts}"
        self.status("Exporting your data to Desktop...")
        target.mkdir(parents=True, exist_ok=True)
        self.export_target = target

        for folder in ("downloads", "previous_downloads"):
            src = INSTALL_DIR / folder
            if src.is_dir():
                self.status(f"Exporting {folder}/ ...")
                shutil.copytree(src, target / folder, dirs_exist_ok=True)

        for fname in ("history.json", "activity.json"):
            src = INSTALL_DIR / fname
            if src.is_file():
                self.status(f"Exporting {fname} ...")
                shutil.copy2(src, target / fname)

    def _kill_process(self):
        self.status("Stopping YT Grab...")
        # v1.18+ has just one app process: YTGrab.exe. Legacy names
        # (YTGrabApp.exe from v1.17's bundled-wrapper and
        # YTGrabSetup.exe from pre-1.17 standalone updater) are still
        # in the list so this uninstaller cleans up cleanly on older
        # installs without the user knowing which version they were on.
        for image in (
            "YTGrab.exe",          # the app (v1.18+, and legacy Flask app <=1.16)
            "YTGrabApp.exe",       # v1.17 bundled-wrapper intermediate
            "YTGrabSetup.exe",     # pre-1.17 standalone updater
        ):
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", image],
                    capture_output=True, text=True, timeout=10,
                    creationflags=_no_window_flag(),
                )
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.8)

    def _close_explorer_windows(self):
        """Close any Explorer windows viewing the install folder.

        Explorer holds an open handle on the directory it's showing,
        which is what blocks rmdir. Doing this in-process via a single
        hidden PowerShell call is cleaner than the v1.8.0 approach of
        shelling out to cmd+ping+powershell (three processes, some of
        which flashed a console on screen).
        """
        self.status("Releasing Explorer handles...")
        ps_script = (
            "$ErrorActionPreference = 'SilentlyContinue'; "
            "$s = New-Object -ComObject Shell.Application; "
            "foreach ($w in @($s.Windows())) { "
            "try { "
            "if ($w.LocationURL -and "
            "($w.LocationURL -match 'Programs[/\\\\]YTGrab')) "
            "{ $w.Quit() } "
            "} catch {} "
            "}"
        )
        try:
            subprocess.run(
                [
                    "powershell", "-NoProfile", "-NonInteractive",
                    "-WindowStyle", "Hidden",
                    "-ExecutionPolicy", "Bypass",
                    "-Command", ps_script,
                ],
                capture_output=True, text=True, timeout=8,
                creationflags=_no_window_flag(),
            )
        except Exception:  # noqa: BLE001
            pass
        # Give Explorer a moment to actually close the windows and
        # release its directory handles before we queue the rmdir.
        time.sleep(0.6)

    def _remove_shortcuts(self):
        self.status("Removing shortcuts...")
        for lnk in SHORTCUTS:
            if lnk and lnk.exists():
                try:
                    lnk.unlink()
                except Exception:  # noqa: BLE001
                    pass

    def _wipe_webview_cache(self):
        """Wipe %LOCALAPPDATA%\\YTGrab\\ -- WebView2's user-data dir
        and any other per-user caches the app wrote outside the install
        folder. Without this the uninstall leaves orphaned localStorage,
        IndexedDB, service worker caches etc. under LOCALAPPDATA.

        Also sweeps %TEMP%\\_MEI* dirs that PyInstaller one-file builds
        extract into -- normally those self-clean on process exit but
        they can linger after a crash.
        """
        self.status("Clearing app cache...")
        if WEBVIEW_DIR and WEBVIEW_DIR.is_dir():
            try:
                shutil.rmtree(WEBVIEW_DIR, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
        # Clean up stale PyInstaller unpack dirs (onefile build leftovers).
        try:
            for entry in TEMP_DIR.iterdir():
                if entry.name.startswith("_MEI") and entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    def _schedule_self_delete(self):
        """Spawn a single hidden PowerShell process to wipe the install
        folder after this uninstaller exits.

        v1.8.1: replaces the .bat + cmd + ping chain with one hidden
        PowerShell call. No cmd window, no ping flashes, one child
        process. PowerShell's Start-Sleep is a built-in cmdlet (not an
        external exe) so it doesn't allocate a console.

        What bit v1.7.1: File Explorer holds an open directory handle
        on whatever folder it's displaying, and the user is guaranteed
        to have Explorer open on the install folder because that's how
        they launched the uninstaller. Windows silently refuses rmdir
        on a directory any process has open.

        Flow:
          1. Wait 2s for this Python process to exit -> releases its
             YTGrabUninstaller.exe file handle.
          2. Kill any lingering YTGrab.exe / YTGrabUninstaller.exe
             just in case.
          3. Retry Remove-Item up to 10 times with 1.5s gaps.
          4. Log everything to %TEMP%\\ytgrab-uninst.log for support.
        """
        self.status("Scheduling final cleanup...")
        target = str(INSTALL_DIR)
        log_path = str(TEMP_DIR / "ytgrab-uninst.log")

        # Double-up backslashes + escape single quotes for the PS literal.
        target_ps  = target.replace("'", "''")
        logpath_ps = log_path.replace("'", "''")

        ps_script = (
            "$ErrorActionPreference = 'SilentlyContinue'; "
            f"$target = '{target_ps}'; "
            f"$log = '{logpath_ps}'; "
            "Set-Content -Path $log -Value \"=== YTGrab uninstall cleanup ===\"; "
            "Add-Content -Path $log -Value \"Start: $(Get-Date)\"; "
            "Add-Content -Path $log -Value \"Target: $target\"; "
            # Wait for the uninstaller Python process to fully exit.
            "Start-Sleep -Seconds 2; "
            # Belt + suspenders: re-kill anything still holding files.
            "Stop-Process -Name YTGrab -Force 2>$null; "
            "Stop-Process -Name YTGrabApp -Force 2>$null; "
            "Stop-Process -Name YTGrabSetup -Force 2>$null; "
            "Stop-Process -Name YTGrabUninstaller -Force 2>$null; "
            "Start-Sleep -Milliseconds 500; "
            # Retry loop: handles released asynchronously, so we poll.
            "for ($i = 1; $i -le 10; $i++) { "
            "  Add-Content -Path $log -Value \"Attempt $i\"; "
            "  Remove-Item -Path $target -Recurse -Force "
            "    -ErrorAction SilentlyContinue; "
            "  if (-not (Test-Path $target)) { break } "
            "  Start-Sleep -Milliseconds 1500 "
            "} "
            "if (Test-Path $target) { "
            "  Add-Content -Path $log -Value "
            "    \"FAILED: folder still exists after 10 attempts\"; "
            "  Add-Content -Path $log -Value "
            "    \"User may need to delete the folder manually.\" "
            "} else { "
            "  Add-Content -Path $log -Value "
            "    \"SUCCESS: folder removed at $(Get-Date)\" "
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
                # CREATE_NO_WINDOW alone -- do NOT add DETACHED_PROCESS
                # as the pair-combination can briefly flash a console
                # on some Win11 builds. -WindowStyle Hidden on the PS
                # side covers us even if a parent console were allocated.
                creationflags=_no_window_flag(),
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001
            self.had_error = True
            self.status(f"Couldn't schedule cleanup: {exc}")


def _no_window_flag() -> int:
    """CREATE_NO_WINDOW so subprocess calls don't flash a cmd box."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --- GUI --------------------------------------------------------------

# Color palette
BG = "#1a1a1a"
FG = "#e8e8e8"
FG_MUTED = "#9a9a9a"
ACCENT = "#a78bfa"          # YT Grab purple
ACCENT_HOVER = "#8b6fe0"
DONE_GREEN = "#4ade80"
LOG_BG = "#0a0a0a"
LOG_FG = "#7a7a7a"


class UninstallerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("YT Grab - Uninstaller")
        W, H = 520, 380
        # Center on the primary monitor. tk's default placement dumps
        # the window in the top-left, which looks unpolished next to
        # the installer's centered ttk progress window.
        try:
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            x = max(0, (sw - W) // 2)
            y = max(0, (sh - H) // 2)
            root.geometry(f"{W}x{H}+{x}+{y}")
        except Exception:  # noqa: BLE001
            root.geometry(f"{W}x{H}")
        root.minsize(480, 360)
        root.configure(bg=BG)

        outer = tk.Frame(root, bg=BG, padx=28, pady=24)
        outer.pack(fill="both", expand=True)

        # --- Header -------------------------------------------------
        self.header_var = tk.StringVar(value="Uninstall YT Grab")
        self.header_lbl = tk.Label(
            outer, textvariable=self.header_var,
            bg=BG, fg=FG,
            font=("Segoe UI Semibold", 16),
        )
        self.header_lbl.pack(anchor="w")

        self.subhead_var = tk.StringVar(
            value="Removes the app, app data, and shortcuts. "
                  "Nothing else on your PC is touched."
        )
        tk.Label(
            outer, textvariable=self.subhead_var,
            bg=BG, fg=FG_MUTED,
            font=("Segoe UI", 9),
            justify="left", wraplength=460,
        ).pack(anchor="w", pady=(4, 16))

        # --- Export checkbox ---------------------------------------
        self.export_var = tk.IntVar(value=1)
        self.export_cb = tk.Checkbutton(
            outer,
            text="Export my downloads + history to the Desktop first",
            variable=self.export_var,
            bg=BG, fg=FG, selectcolor=BG,
            activebackground=BG, activeforeground=FG,
            highlightthickness=0, borderwidth=0,
            font=("Segoe UI", 10),
            anchor="w",
        )
        self.export_cb.pack(anchor="w", pady=(0, 18))

        # --- Big primary button ------------------------------------
        self.uninstall_btn = tk.Button(
            outer, text="Uninstall YT Grab",
            bg=ACCENT, fg="#ffffff",
            activebackground=ACCENT_HOVER, activeforeground="#ffffff",
            relief="flat", borderwidth=0,
            font=("Segoe UI Semibold", 11),
            padx=18, pady=10,
            cursor="hand2",
            command=self._on_uninstall,
        )
        self.uninstall_btn.pack(fill="x", pady=(0, 8))

        self.cancel_btn = tk.Button(
            outer, text="Cancel",
            bg=BG, fg=FG_MUTED,
            activebackground=BG, activeforeground=FG,
            relief="flat", borderwidth=0,
            font=("Segoe UI", 9),
            cursor="hand2",
            command=self._on_cancel,
        )
        self.cancel_btn.pack(pady=(0, 12))

        # --- Progress bar (hidden until the user clicks Uninstall) --
        # Accent-colored progress bar. ttk needs the clam theme to let
        # us recolor the trough + fill.
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:  # noqa: BLE001
            pass
        style.configure(
            "YT.Horizontal.TProgressbar",
            background=ACCENT, troughcolor="#2a2a2a",
            bordercolor=BG, lightcolor=ACCENT, darkcolor=ACCENT,
            thickness=6,
        )
        style.configure(
            "YTDone.Horizontal.TProgressbar",
            background=DONE_GREEN, troughcolor="#2a2a2a",
            bordercolor=BG, lightcolor=DONE_GREEN, darkcolor=DONE_GREEN,
            thickness=6,
        )
        self.progress = ttk.Progressbar(
            outer, style="YT.Horizontal.TProgressbar",
            maximum=1.0, length=460, mode="determinate",
        )
        # Pack but stay invisible for the pre-uninstall state. We reveal
        # it on button-click. pack_forget removes but preserves options.
        self.progress.pack(fill="x", pady=(4, 6))
        self.progress.pack_forget()

        # --- Status line --------------------------------------------
        self.status_var = tk.StringVar(value="")
        tk.Label(
            outer, textvariable=self.status_var,
            bg=BG, fg=LOG_FG,
            font=("Consolas", 9),
            anchor="w", justify="left",
            wraplength=460,
        ).pack(anchor="w", fill="x")

        self.worker_thread: threading.Thread | None = None

    # -- UI-thread marshalling helpers --------------------------------

    def _set_status(self, msg: str):
        self.root.after(0, self.status_var.set, msg)

    def _set_progress(self, frac: float):
        self.root.after(0, self._do_set_progress, float(frac))

    def _do_set_progress(self, frac: float):
        self.progress["value"] = max(0.0, min(1.0, frac))

    # -- button handlers ----------------------------------------------

    def _on_cancel(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return
        self.root.destroy()

    def _on_uninstall(self):
        # The big purple "Uninstall YT Grab" button IS the confirmation.
        # v1.8.0's extra messagebox.askyesno was a double-prompt that
        # made the UI feel nagging -- users click the button, they've
        # decided. Skip it.
        # Disable the controls + reveal the progress bar.
        self.uninstall_btn.configure(
            state="disabled",
            bg="#3a3a3a", fg=FG_MUTED,
            text="Uninstalling...",
        )
        self.cancel_btn.configure(state="disabled")
        self.export_cb.configure(state="disabled")
        self.progress.pack(fill="x", pady=(4, 6))

        worker = UninstallerWorker(
            status_cb=self._set_status,
            progress_cb=self._set_progress,
            done_cb=self._on_done,
            export_first=bool(self.export_var.get()),
        )
        self.worker_thread = threading.Thread(
            target=worker.run, daemon=True
        )
        self.worker_thread.start()

    def _on_done(self, had_error: bool, export_target: Path | None):
        self.root.after(
            0, self._on_done_main_thread, had_error, export_target
        )

    def _on_done_main_thread(
        self, had_error: bool, export_target: Path | None
    ):
        if had_error:
            self.header_var.set("Uninstall finished with errors")
            self.subhead_var.set(
                "Some steps didn't complete. See "
                "%TEMP%\\ytgrab-uninst.log for details."
            )
            self.cancel_btn.configure(state="normal", text="Close")
            return

        # Success state: swap the bar to green, tick the header, and
        # replace the uninstall button with a prominent "Done" banner.
        self.progress.configure(style="YTDone.Horizontal.TProgressbar")
        self.header_var.set("Uninstall complete")
        if export_target:
            self.subhead_var.set(
                f"Your data was exported to Desktop\\{export_target.name}\\. "
                "The app folder will finish cleaning up in a moment."
            )
        else:
            self.subhead_var.set(
                "YT Grab has been removed. The app folder will finish "
                "cleaning up in a moment."
            )
        self.uninstall_btn.configure(
            state="normal",
            bg=DONE_GREEN, fg="#0a0a0a",
            activebackground=DONE_GREEN, activeforeground="#0a0a0a",
            text="Done \u2713",
            command=self.root.destroy,
        )
        self.cancel_btn.configure(state="normal", text="Close")
        self._set_status("")


# --- Entrypoint -------------------------------------------------------

def main():
    if sys.platform != "win32":
        print("This uninstaller only runs on Windows.")
        sys.exit(1)

    root = tk.Tk()
    UninstallerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
