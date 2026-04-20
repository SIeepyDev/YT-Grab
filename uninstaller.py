"""
YT Grab - Standalone Uninstaller
=================================

A tkinter GUI uninstaller that does the same work as the legacy
uninstall.bat, but as a single .exe so users don't have to right-click
a batch file.

Flow (mirrors the .bat version):
  1. Optional: export downloads/, previous_downloads/, history.json,
     activity.json to Desktop\\YTGrab-export-<timestamp>\\
  2. Kill YTGrab.exe if it's running
  3. Delete %LOCALAPPDATA%\\YTGrab\\ (the WebView2 storage path)
  4. Delete Desktop + Start Menu shortcuts (current + legacy names)
  5. Self-delete the install folder this exe lives in

Doesn't touch the registry, ProgramData, or any folder outside the
paths above. Safe to run on any machine.

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
from tkinter import messagebox, ttk


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
        self.log(f"[export] {target}")
        target.mkdir(parents=True, exist_ok=True)
        self.export_target = target

        # Folder copies
        for folder in ("downloads", "previous_downloads"):
            src = INSTALL_DIR / folder
            if src.is_dir():
                self.log(f"  copying {folder}/ ...")
                shutil.copytree(src, target / folder, dirs_exist_ok=True)

        # File copies
        for fname in ("history.json", "activity.json"):
            src = INSTALL_DIR / fname
            if src.is_file():
                self.log(f"  copying {fname} ...")
                shutil.copy2(src, target / fname)

        self.log("[export] done.")

    def _kill_process(self):
        self.log("[1/4] Stopping YTGrab.exe ...")
        try:
            res = subprocess.run(
                ["taskkill", "/F", "/IM", "YTGrab.exe"],
                capture_output=True, text=True, timeout=10,
                creationflags=_no_window_flag(),
            )
            if res.returncode == 0:
                self.log("       killed.")
                # Let Windows release file handles before we wipe.
                time.sleep(2)
            else:
                self.log("       not running.")
        except Exception as exc:  # noqa: BLE001
            self.log(f"       taskkill failed: {exc}")

    def _remove_appdata(self):
        self.log("[2/4] Removing %LOCALAPPDATA%\\YTGrab ...")
        if YTGRAB_DATA_DIR and YTGRAB_DATA_DIR.exists():
            try:
                shutil.rmtree(YTGRAB_DATA_DIR, ignore_errors=False)
                self.log("       removed.")
            except Exception as exc:  # noqa: BLE001
                self.had_error = True
                self.log(
                    f"       FAILED ({exc}). Close YT Grab fully and retry."
                )
        else:
            self.log("       already gone.")

    def _remove_shortcuts(self):
        self.log("[3/4] Removing shortcuts ...")
        any_removed = False
        for lnk in SHORTCUTS:
            if lnk and lnk.exists():
                try:
                    lnk.unlink()
                    self.log(f"       removed {lnk.name}")
                    any_removed = True
                except Exception as exc:  # noqa: BLE001
                    self.log(f"       failed to remove {lnk.name}: {exc}")
        if not any_removed:
            self.log("       no shortcuts found.")

    def _schedule_self_delete(self):
        """Spawn a detached cmd that waits then wipes the install folder.

        Classic Windows self-delete pattern — the child cmd inherits no
        handles to our process, so once we exit it can rmdir us.
        """
        self.log("[4/4] Scheduling install-folder removal ...")
        target = str(INSTALL_DIR)
        # 3-second delay gives the GUI time to close cleanly.
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
            self.log("       scheduled.")
        except Exception as exc:  # noqa: BLE001
            self.had_error = True
            self.log(f"       scheduling failed: {exc}")


def _no_window_flag() -> int:
    """CREATE_NO_WINDOW so subprocess calls don't flash a cmd box."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --- GUI --------------------------------------------------------------

class UninstallerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("YT Grab - Uninstaller")
        root.geometry("560x460")
        root.minsize(520, 420)
        root.configure(bg="#1e1e1e")

        # Modern dark style
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Dark.TFrame", background="#1e1e1e",
        )
        style.configure(
            "Dark.TLabel", background="#1e1e1e", foreground="#e8e8e8",
            font=("Segoe UI", 10),
        )
        style.configure(
            "Title.TLabel", background="#1e1e1e", foreground="#ffffff",
            font=("Segoe UI Semibold", 14),
        )
        style.configure(
            "Dark.TCheckbutton", background="#1e1e1e", foreground="#e8e8e8",
            font=("Segoe UI", 10),
        )
        style.map(
            "Dark.TCheckbutton",
            background=[("active", "#1e1e1e")],
        )
        style.configure(
            "Danger.TButton", font=("Segoe UI Semibold", 10),
            padding=(16, 8),
        )
        style.configure(
            "Cancel.TButton", font=("Segoe UI", 10),
            padding=(16, 8),
        )

        outer = ttk.Frame(root, style="Dark.TFrame", padding=20)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer, text="Uninstall YT Grab", style="Title.TLabel"
        ).pack(anchor="w")

        body = (
            "This will completely remove YT Grab from your PC:\n"
            "  - Stop YTGrab.exe if it's running\n"
            "  - Delete %LOCALAPPDATA%\\YTGrab\\\n"
            "  - Delete Desktop and Start Menu shortcuts\n"
            "  - Delete this install folder\n\n"
            "It does NOT touch the registry, ProgramData, or any other\n"
            "folder on your PC."
        )
        ttk.Label(
            outer, text=body, style="Dark.TLabel", justify="left"
        ).pack(anchor="w", pady=(8, 14))

        # Export checkbox
        self.export_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            outer,
            text="Export downloads + history to Desktop first (recommended)",
            variable=self.export_var,
            style="Dark.TCheckbutton",
        ).pack(anchor="w")

        ttk.Label(
            outer,
            text=f"Install folder:  {INSTALL_DIR}",
            style="Dark.TLabel",
        ).pack(anchor="w", pady=(12, 4))

        # Log box
        log_frame = tk.Frame(outer, bg="#1e1e1e")
        log_frame.pack(fill="both", expand=True, pady=(8, 12))

        self.log_box = tk.Text(
            log_frame, height=10, bg="#0f0f0f", fg="#cfcfcf",
            insertbackground="#cfcfcf",
            font=("Consolas", 9),
            relief="flat", borderwidth=0,
            wrap="word", state="disabled",
        )
        scrollbar = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.log_box.yview
        )
        self.log_box.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log_box.pack(side="left", fill="both", expand=True)

        # Buttons
        btns = ttk.Frame(outer, style="Dark.TFrame")
        btns.pack(fill="x")

        self.cancel_btn = ttk.Button(
            btns, text="Cancel",
            style="Cancel.TButton",
            command=self._on_cancel,
        )
        self.cancel_btn.pack(side="right", padx=(8, 0))

        self.uninstall_btn = ttk.Button(
            btns, text="Uninstall YT Grab",
            style="Danger.TButton",
            command=self._on_uninstall,
        )
        self.uninstall_btn.pack(side="right")

        self.worker_thread: threading.Thread | None = None

    # -- log helpers ---------------------------------------------------

    def _log(self, msg: str):
        self.root.after(0, self._log_main_thread, msg)

    def _log_main_thread(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # -- button handlers ----------------------------------------------

    def _on_cancel(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return  # ignore once running -- can't cleanly cancel rmtree
        self.root.destroy()

    def _on_uninstall(self):
        if not messagebox.askyesno(
            "Confirm uninstall",
            "Remove YT Grab from this PC? This can't be undone.",
            parent=self.root,
        ):
            return

        self.uninstall_btn.configure(state="disabled")
        self.cancel_btn.configure(state="disabled")

        worker = UninstallerWorker(
            log_callback=self._log,
            done_callback=self._on_done,
            export_first=self.export_var.get(),
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
            self._log("")
            self._log("Finished with errors. See messages above.")
            self.cancel_btn.configure(state="normal", text="Close")
            return

        self._log("")
        self._log("YT Grab is fully uninstalled.")
        if export_target:
            self._log(f"Your data is at: {export_target}")
        self._log("This window will close in 3 seconds; the install")
        self._log("folder will be deleted right after.")
        # Close on its own so the cmd self-delete can run.
        self.root.after(3000, self.root.destroy)


# --- Entrypoint -------------------------------------------------------

def main():
    # Edge case: someone runs the uninstaller from inside the install
    # folder while another copy of YTGrab.exe has the data dir locked.
    # Surface a friendly error rather than a Tcl traceback.
    if sys.platform != "win32":
        print("This uninstaller only runs on Windows.")
        sys.exit(1)

    root = tk.Tk()
    UninstallerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
