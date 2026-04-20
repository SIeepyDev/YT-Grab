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
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", "")).resolve()
USERPROFILE = Path(os.environ.get("USERPROFILE", "")).resolve()
APPDATA = Path(os.environ.get("APPDATA", "")).resolve()

YTGRAB_DATA_DIR = LOCALAPPDATA / "YTGrab" if LOCALAPPDATA else None
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
            self._remove_appdata()
            self._remove_shortcuts()
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
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "YTGrab.exe"],
                capture_output=True, text=True, timeout=10,
                creationflags=_no_window_flag(),
            )
            time.sleep(1)
        except Exception:  # noqa: BLE001
            pass

    def _remove_appdata(self):
        self.log("Removing app data ...")
        if YTGRAB_DATA_DIR and YTGRAB_DATA_DIR.exists():
            try:
                shutil.rmtree(YTGRAB_DATA_DIR, ignore_errors=False)
            except Exception as exc:  # noqa: BLE001
                self.had_error = True
                self.log(
                    f"  FAILED ({exc}). Close YT Grab fully and retry."
                )

    def _remove_shortcuts(self):
        self.log("Removing shortcuts ...")
        for lnk in SHORTCUTS:
            if lnk and lnk.exists():
                try:
                    lnk.unlink()
                except Exception:  # noqa: BLE001
                    pass

    def _schedule_self_delete(self):
        """Spawn a detached cmd that waits then wipes the install folder.

        Classic Windows self-delete pattern -- the child cmd inherits no
        handles to our process, so once we exit it can rmdir us.
        """
        self.log("Scheduling install folder removal ...")
        target = str(INSTALL_DIR)
        cmd = (
            f'timeout /t 3 /nobreak >nul & '
            f'rmdir /s /q "{target}"'
        )
        try:
            subprocess.Popen(
                ["cmd", "/c", cmd],
                creationflags=(
                    _no_window_flag()
                    | getattr(subprocess, "DETACHED_PROCESS", 0)
                ),
                close_fds=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.had_error = True
            self.log(f"  scheduling failed: {exc}")


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
                "Finished with errors. Close YT Grab and run again."
            )
            self.cancel_btn.configure(state="normal", text="Close")
            return

        if export_target:
            self._set_status(
                f"Done. Your data is at Desktop\\{export_target.name}\\. "
                "Closing in 3s..."
            )
        else:
            self._set_status("Done. Closing in 3s...")
        self.root.after(3000, self.root.destroy)


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
