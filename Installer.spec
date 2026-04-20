# PyInstaller spec for the YT Grab online installer / auto-updater.
#
# Produces dist/YTGrabSetup.exe -- a tiny single-file Windows binary
# that pulls the real app from github.com/SIeepyDev/YTGrab releases
# on every launch. This is the .exe friends download from the public
# repo; it does the heavy lifting at runtime so the published binary
# stays small.
#
# Stdlib + Tkinter only at runtime -> build is ~10 MB.
#
# Build with:  build.bat (calls this after the main app + uninstaller)

# -*- mode: python ; coding: utf-8 -*-

import os

_ICON_PATH = 'icon.ico' if os.path.isfile('icon.ico') else None

a = Analysis(
    ['installer.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Aggressive trim -- the installer is stdlib-only. Nothing
        # from the main app's dependency graph should come along for
        # the ride.
        'matplotlib', 'numpy', 'PIL', 'pandas', 'scipy',
        'IPython', 'jupyter', 'notebook', 'pytest', 'sphinx',
        'yt_dlp', 'flask', 'webview', 'imageio_ffmpeg',
        'youtube_transcript_api', 'comtypes',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='YTGrabSetup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX makes Defender suspicious; same call as main exe.
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # GUI app -- no flashing cmd window.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON_PATH,
)
