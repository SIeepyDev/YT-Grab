# PyInstaller spec for the YT Grab inner app onefile build.
#
# Produces dist/YTGrabApp.exe -- a single-file Windows executable that
# embeds Python, all deps, index.html, and ffmpeg.exe (via imageio-ffmpeg).
# This binary is NOT the one friends download. It is bundled as a data
# resource inside the single public YTGrab.exe (see Installer.spec), which
# extracts it to the install folder on first run and on every update.
#
# Build with:    build.bat
# Run with:      never directly -- launched by the outer YTGrab.exe

# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Optional custom icon. Drop an `icon.ico` next to this spec file and the
# build will pick it up automatically. If no icon.ico is present the exe
# falls back to PyInstaller's default Python icon.
_ICON_PATH = 'icon.ico' if os.path.isfile('icon.ico') else None

# yt-dlp has hundreds of site-specific extractor modules it loads lazily.
# PyInstaller doesn't catch those through static analysis, so we force it
# to bundle every submodule. Same for youtube_transcript_api which also
# does dynamic lookups.
hidden = []
hidden += collect_submodules('yt_dlp')
hidden += collect_submodules('yt_dlp.extractor')
hidden += collect_submodules('yt_dlp.postprocessor')
hidden += collect_submodules('youtube_transcript_api')
# pywebview pulls in its platform-specific backends dynamically, PyInstaller
# can't see them through static analysis.
hidden += collect_submodules('webview')
hidden += ['webview.platforms.edgechromium', 'webview.platforms.mshtml',
           'webview.platforms.winforms']
# comtypes is used for the Explorer-window-reuse check.
hidden += collect_submodules('comtypes')

# Bundle the imageio-ffmpeg package's binary ffmpeg.exe. The package
# includes it under imageio_ffmpeg/binaries/ -- collect_data_files pulls
# both the Python stubs and the binary.
datas = []
datas += [('index.html', '.')]
# Ship icon.ico alongside the bundled resources so the frozen exe can
# read it at runtime (for browser favicon + console window icon). The
# exe's own application icon is set separately via the EXE(icon=...)
# directive below -- that's a static embed into the PE header.
if _ICON_PATH:
    datas += [(_ICON_PATH, '.')]
# Bundle the bin/ folder (ffmpeg.exe + ffprobe.exe from fetch_ffmpeg.bat)
# so the frozen exe has full thumbnail/metadata embedding support out of
# the box. server.py looks for bin/ next to the exe AND inside the
# PyInstaller extraction dir -- either works.
if os.path.isdir('bin'):
    for fname in ('ffmpeg.exe', 'ffprobe.exe'):
        fpath = os.path.join('bin', fname)
        if os.path.isfile(fpath):
            datas += [(fpath, 'bin')]
datas += collect_data_files('imageio_ffmpeg')
# pywebview ships a small amount of JS for its Python<->JS bridge.
datas += collect_data_files('webview')

a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim fat we never use to keep the exe smaller.
        'tkinter', 'matplotlib', 'numpy', 'PIL', 'pandas', 'scipy',
        'IPython', 'jupyter', 'notebook', 'pytest', 'sphinx',
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
    name='YTGrabApp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX compression makes Windows Defender more suspicious
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # windowless app -- no console window ever appears.
                         # Server lifecycle is controlled by the browser tab
                         # (close tab -> heartbeat timeout -> auto-shutdown).
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON_PATH,  # None if no icon.ico present; PyInstaller uses default in that case
)
