"""
YT Grab - Standalone Uninstaller
=================================

A tkinter GUI uninstaller. Single big button -- click and it does
everything: optional export, kill app, wipe app data, remove shortcuts,
self-delete the install folder.

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
from tkinter import messagebox


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
TEMP_DIR = Path(os.environ.get("TEMP", r"C:\Windows\Temp"))

DESKTOP = USERPROFILE / "Desktop" if USERPROFILE else None
START_MENU = (
    APPDATA / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    if APPDATA
    else None
)

SHORTCUTS = []
if DESKTOP:
    SHORTCUTS += [DESKTOP / "YT Grab.lnk", DESKTOP / "YT Downloader.lnk"]
if START_MENU:
    SHORTCUTS += [
        START_MENU / "YT Grab.lnk",
        START_MENU / "YT Downloader.lnk",
    ]


# --- Worker logic -----------------------------------------------------

class UninstallerWorker:
    """Runs the actual uninstall steps off the UI thread."""

    def __init__(self, log_callback, done_callback, export_first: bool):
        self.log = log_callback
        self.done = done_callback
        self.export_first = export_first
        self.export_target: Path | None = None
        self.had_error = False

    def run(self):
        try:
            if self.export_first:
                self._export_data()
            self._kill_process()
            self._remove_shortcuts()
            # The install folder IS the "app data" folder in the
            # bootstrapper-based install (everything lives at
            # %LOCALAPPDATA%\Programs\YTGrab\), so a single self-delete
            # of INSTALL_DIR wipes downloads/, previous_downloads/,
            # debug.log, version.txt and the three .exes in one shot.
            self._schedule_self_delete()
        except Exception as exc:  # noqa: BLE001
            self.had_error = True
            self.log(f"ERROR: {exc}")
        finally:
            self.done(self.had_error, self.export_target)

    # -- steps ---------------------------------------------------------

    def _export_data(self):
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        target = (DESKTOP or INSTALL_DIR) / f"YTGrab-export-{ts}"
        self.log(f"Exporting your data to {target.name}/ ...")
        target.mkdir(parents=True, exist_ok=True)
        self.export_target = target

        for folder in ("downloads", "previous_downloads"):
            src = INSTALL_DIR / folder
            if src.is_dir():
                self.log(f"  {folder}/")
                shutil.copytree(src, target / folder, dirs_exist_ok=True)

        for fname in ("history.json", "activity.json"):
            src = INSTALL_DIR / fname
            if src.is_file():
                self.log(f"  {fname}")
                shutil.copy2(src, target / fname)

    def _kill_process(self):
        self.log("Stopping YTGrab.exe ...")
        for image in ("YTGrab.exe",):
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", image],
                    capture_output=True, text=True, timeout=10,
                    creationflags=_no_window_flag(),
                )
            except Exception:  # noqa: BLE001
                pass
        time.sleep(1)

    def _remove_shortcuts(self):
        self.log("Removing shortcuts ...")
        for lnk in SHORTCUTS:
            if lnk and lnk.exists():
                try:
                    lnk.unlink()
                except Exception:  # noqa: BLE001
                    pass

    def _schedule_self_delete(self):
        """Spawn a detached batch that wipes the install folder.

        What bit v1.7.1: File Explorer holds an open directory handle
        on whatever folder it's displaying, and the user is guaranteed
        to have Explorer open on the install folder because that's
        how they launched the uninstaller in the first place. Windows
        silently refuses rmdir on a directory any process has open, so
        the v1.7.1 rmdir ran and did nothing.

        This version writes a self-contained .bat to %TEMP% that:
          1. Logs every step to %TEMP%\\ytgrab-uninst.log so we can
             actually diagnose future failures.
          2. Waits for this Python process to exit.
          3. Asks any File Explorer windows viewing the install folder
             (or a subfolder of it) to close, via the Shell.Application
             COM object.
          4. Kills any lingering YTGrab.exe / YTGrabUninstaller.exe.
          5. Retries rmdir up to 10 times with 3s gaps -- handles still
             pending from the closed Explorer windows clear on their
             own schedule, so we poll.
          6. Self-deletes the .bat.

        Using a written-out .bat (instead of cmd /c "long string")
        gives us control over line-by-line behavior and, critically,
        lets us use delayed expansion for the retry counter.
        """
        self.log("Scheduling install folder removal ...")
        target = str(INSTALL_DIR).rstrip("\\")
        bat_path = TEMP_DIR / f"ytgrab-uninst-{os.getpid()}.bat"
        log_path = TEMP_DIR / "ytgrab-uninst.log"

        # PowerShell one-liner that walks every open Shell window and
        # closes the ones whose LocationURL points at our install
        # folder (or anything beneath it). Match is on the tail
        # "Programs/YTGrab" or "Programs\YTGrab" -- specific enough
        # that we don't hit unrelated windows, loose enough that it
        # catches a user drilled into downloads/ or previous_downloads/.
        ps_close = (
            "$s = New-Object -ComObject Shell.Application; "
            "foreach ($w in @($s.Windows())) { "
            "try { "
            "if ($w.LocationURL -and "
            "($w.LocationURL -match 'Programs[/\\\\]YTGrab')) "
            "{ $w.Quit() } "
            "} catch {} "
            "}"
        )

        bat_lines = [
            "@echo off",
            "setlocal enabledelayedexpansion",
            f'echo === YTGrab uninstall cleanup === > "{log_path}"',
            f'echo Start: %DATE% %TIME%    >> "{log_path}"',
            f'echo Target: {target}       >> "{log_path}"',
            "",
            "REM Wait for the uninstaller Python process to fully exit",
            "REM so its own YTGrabUninstaller.exe file handle releases.",
            "ping -n 4 127.0.0.1 >nul",
            "",
            "REM Close any File Explorer windows viewing the install",
            "REM folder. Explorer holds an open handle on the directory",
            "REM it's showing, which blocks rmdir -- this was the v1.7.1",
            "REM silent-failure bug.",
            f'echo Closing Explorer windows... >> "{log_path}"',
            (
                f'powershell -NoProfile -Command "{ps_close}" '
                f'>> "{log_path}" 2>&1'
            ),
            "",
            "REM Belt + suspenders -- kill anything still holding files.",
            "REM taskkill returns nonzero when the process isn't running,",
            "REM which is fine, so swallow both streams.",
            'taskkill /F /IM YTGrab.exe           >nul 2>&1',
            'taskkill /F /IM YTGrabUninstaller.exe >nul 2>&1',
            "",
            "REM Give Explorer + the killed processes a beat to release",
            "REM their file handles. Windows doesn't expose the release",
            "REM synchronously, so we poll via the retry loop below.",
            "ping -n 3 127.0.0.1 >nul",
            "",
            "set count=0",
            ":retry",
            "set /a count+=1",
            f'echo Attempt !count!: rmdir /s /q "{target}" >> "{log_path}"',
            f'rmdir /s /q "{target}" >> "{log_path}" 2>&1',
            f'if not exist "{target}" goto done',
            "if !count! geq 10 goto fail",
            "ping -n 3 127.0.0.1 >nul",
            "goto retry",
            "",
            ":done",
            f'echo SUCCESS: folder removed at %DATE% %TIME% >> "{log_path}"',
            "goto cleanup",
            "",
            ":fail",
            (
                f'echo FAILED: folder still exists after 10 attempts '
                f'at %DATE% %TIME% >> "{log_path}"'
            ),
            (
                f'echo User may need to delete "{target}" manually. '
                f'>> "{log_path}"'
            ),
            "",
            ":cleanup",
            "endlocal",
            "REM Self-delete the batch file. The (goto) 2>nul trick",
            "REM makes cmd release its read lock on this .bat before",
            "REM the del runs -- without it, del fails silently.",
            '(goto) 2>nul & del "%~f0"',
        ]
        bat_content = "\r\n".join(bat_lines) + "\r\n"

        try:
            bat_path.write_text(bat_content, encoding="ascii")
        except Exception as exc:  # noqa: BLE001
            self.had_error = True
            self.log(f"  couldn't stage cleanup script: {exc}")
            return

        try:
            subprocess.Popen(
                ["cmd", "/c", str(bat_path)],
                cwd=(os.environ.get("SystemDrive", "C:") + "\\"),
                creationflags=(
                    _no_window_flag()
                    | getattr(subprocess, "DETACHED_PROCESS", 0)
                ),
                close_fds=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.had_error = True
            self.log(f"  couldn't launch cleanup script: {exc}")


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
LOG_BG = "#0a0a0a"
LOG_FG = "#7a7a7a"


class UninstallerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("YT Grab - Uninstaller")
        root.geometry("520x340")
        root.minsize(480, 320)
        root.configure(bg=BG)

        outer = tk.Frame(root, bg=BG, padx=28, pady=24)
        outer.pack(fill="both", expand=True)

        # --- Header -------------------------------------------------
        tk.Label(
            outer, text="Uninstall YT Grab",
            bg=BG, fg=FG,
            font=("Segoe UI Semibold", 16),
        ).pack(anchor="w")

        tk.Label(
            outer,
            text="Removes the app, app data, and shortcuts. "
                 "Nothing else on your PC is touched.",
            bg=BG, fg=FG_MUTED,
            font=("Segoe UI", 9),
            justify="left", wraplength=460,
        ).pack(anchor="w", pady=(4, 16))

        # --- Export checkbox ---------------------------------------
        # Plain tk.Checkbutton (not ttk) so we can fully control the
        # look on Windows -- ttk's clam theme renders the indicator
        # as a weird "X" character on some systems.
        self.export_var = tk.IntVar(value=1)
        tk.Checkbutton(
            outer,
            text="Export my downloads + history to the Desktop first",
            variable=self.export_var,
            bg=BG, fg=FG, selectcolor=BG,
            activebackground=BG, activeforeground=FG,
            highlightthickness=0, borderwidth=0,
            font=("Segoe UI", 10),
            anchor="w",
        ).pack(anchor="w", pady=(0, 18))

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
        self.cancel_btn.pack(pady=(0, 14))

        # --- Tiny status line --------------------------------------
        # Single label that gets overwritten as steps run. No giant
        # log panel -- the user doesn't need a forensic trace, they
        # need to know it's working.
        self.status_var = tk.StringVar(value="")
        tk.Label(
            outer, textvariable=self.status_var,
            bg=BG, fg=LOG_FG,
            font=("Consolas", 9),
            anchor="w", justify="left",
            wraplength=460,
        ).pack(anchor="w", fill="x")

        self.worker_thread: threading.Thread | None = None

    # -- log helpers ---------------------------------------------------

    def _log(self, msg: str):
        # Marshal to UI thread.
        self.root.after(0, self._set_status, msg)

    def _set_status(self, msg: str):
        self.status_var.set(msg)

    # -- button handlers ----------------------------------------------

    def _on_cancel(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return
        self.root.destroy()

    def _on_uninstall(self):
        if not messagebox.askyesno(
            "Confirm uninstall",
            "Remove YT Grab from this PC? This can't be undone.",
            parent=self.root,
        ):
            return

        self.uninstall_btn.configure(
            state="disabled",
            bg="#3a3a3a", fg=FG_MUTED,
            text="Uninstalling ...",
        )
        self.cancel_btn.configure(state="disabled")

        worker = UninstallerWorker(
            log_callback=self._log,
            done_callback=self._on_done,
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
            self._set_status(
                "Finished with errors. See %TEMP%\\ytgrab-uninst.log"
            )
            self.cancel_btn.configure(state="normal", text="Close")
            return

        if export_target:
            self._set_status(
                f"Done. Your data is at Desktop\\{export_target.name}\\. "
                "Closing..."
            )
        else:
            self._set_status("Done. Closing...")
        # Close quickly -- the detached cleanup script handles the
        # real wait (~8s) before it wipes the folder, so we don't
        # need the uninstaller window to linger.
        self.root.after(800, self.root.destroy)


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