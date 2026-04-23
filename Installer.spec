# PyInstaller spec for the YT Grab single-file public download.
#
# Produces dist/YTGrab.exe -- the ONE .exe friends download from the
# public GitHub release. It bundles the real app (YTGrabApp.exe) and
# the standalone uninstaller (YTGrabUninstaller.exe) as data resources
# and extracts them to %LOCALAPPDATA%\Programs\YTGrab on first run.
#
# On subsequent launches (via the installed shortcut that points at this
# same outer exe) it does a GitHub releases-latest check and, if a newer
# version exists, downloads the newer YTGrab.exe and swaps itself in
# place before launching the app. The auto-update pattern is the same
# as before; the novelty is that there is now only a single .exe asset
# on each release -- no more YTGrab.exe / YTGrabUninstaller.exe /
# YTGrabSetup.exe trio that confused users into clicking the wrong one.
#
# Stdlib + Tkinter only at runtime. The bundled inner exes are the
# reason this build is ~60-70 MB instead of the ~10 MB the installer
# used to be -- an acceptable tradeoff for "one file, done."
#
# Build with:  build.bat  (calls this AFTER building YTGrabApp.exe
# and YTGrabUninstaller.exe, since they are bundled as datas here)

# -*- mode: python ; coding: utf-8 -*-

import os

_ICON_PATH = 'icon.ico' if os.path.isfile('icon.ico') else None

# Bundle the two inner binaries as PyInstaller data files. They get
# extracted to sys._MEIPASS at runtime, where installer.py finds them
# and copies them into the install folder. build.bat guarantees both
# exist in dist/ before this spec is processed.
_datas = []
for _inner in ('YTGrabApp.exe', 'YTGrabUninstaller.exe'):
    _inner_path = os.path.join('dist', _inner)
    if not os.path.isfile(_inner_path):
        raise SystemExit(
            f"[Installer.spec] Missing {_inner_path}. build.bat builds "
            f"the inner exes first -- run it from repo root, not this "
            f"spec directly."
        )
    _datas.append((_inner_path, '.'))

a = Analysis(
    ['installer.py'],
    pathex=[],
    binaries=[],
    datas=_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Aggressive trim -- the installer itself is stdlib-only.
        # The bundled inner exes bring their own deps baked in; nothing
        # from the app's dependency graph should be linked into the
        # outer shell's Python runtime.
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
    name='YTGrab',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX makes Defender suspicious; same call as inner app.
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # GUI installer -- no flashing cmd window.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON_PATH,
)
