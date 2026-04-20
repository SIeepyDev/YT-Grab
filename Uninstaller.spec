# PyInstaller spec for the YT Grab standalone uninstaller.
#
# Produces dist/YTGrabUninstaller.exe -- a single-file Windows binary
# that ships alongside YTGrab.exe in the package zip. Tkinter-based GUI,
# uses only Python stdlib at runtime so the build is small (~10 MB).
#
# Build with:  build.bat (calls this after building the main app)

# -*- mode: python ; coding: utf-8 -*-

import os

# Reuse the main app's icon if it exists so the uninstaller looks like
# part of the same product. Falls back to PyInstaller's default Python
# icon if icon.ico hasn't been generated yet.
_ICON_PATH = 'icon.ico' if os.path.isfile('icon.ico') else None

a = Analysis(
    ['uninstaller.py'],
    pathex=[],
    binaries=[],
    datas=[],
    # Tkinter is the only non-stdlib dep we touch and PyInstaller picks
    # it up automatically. No yt_dlp / pywebview / flask cruft.
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Aggressive trim -- the uninstaller is stdlib-only.
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
    name='YTGrabUninstaller',
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
