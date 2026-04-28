# PyInstaller spec for YT Grab.
#
# Produces dist/YTGrab.exe -- the single public download. One .exe
# IS the app: on first run it installs itself to %LOCALAPPDATA%,
# fetches YTGrabUninstaller.exe from the GitHub release to sit next
# to it, creates shortcuts, and launches. On subsequent launches it
# does a silent update check and self-replaces if a newer release
# is available, then runs the normal Flask + pywebview UI.
#
# Build with:    build.bat
# Run with:      double-click dist\YTGrab.exe

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
# installer.py provides bootstrap_or_update() -- make sure PyInstaller
# pulls it in (server.py imports it lazily inside __main__ so static
# analysis can miss it).
hidden += ['installer']

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

# Bundle the standalone YTGrabUninstaller.exe inside YTGrab.exe so
# the install flow can extract it locally instead of downloading from
# GitHub. The download approach broke pre-release testing: a v1.19.1
# YTGrab.exe installed against a GitHub releases/latest that still
# pointed at v1.18.0 would download the v1.18 uninstaller -- missing
# new fields the v1.19 installer expected (registry deletion, etc.).
# Bundling guarantees the uninstaller version always matches the
# YTGrab.exe that installed it. build.bat MUST build Uninstaller.spec
# BEFORE YTGrab.spec so this file exists at PyInstaller analysis time.
_uninst = os.path.join('dist', 'YTGrabUninstaller.exe')
if not os.path.isfile(_uninst):
    raise SystemExit(
        f"[YTGrab.spec] Missing {_uninst}. build.bat must build "
        f"Uninstaller.spec FIRST so YTGrabUninstaller.exe exists "
        f"when this spec runs."
    )
datas += [(_uninst, '.')]

# Bundle the release-please manifest so installer.py's APP_VERSION
# reader can pull the version directly from release-please's source
# of truth. Stops the "registry shows 1.19.1, manifest says 1.20.0"
# desync from ever recurring.
_manifest = '.release-please-manifest.json'
if not os.path.isfile(_manifest):
    raise SystemExit(
        f"[YTGrab.spec] Missing {_manifest}. This file is checked "
        f"into the repo and tracks the release version. If you're "
        f"building from a tarball without it, copy from the source "
        f"git checkout."
    )
datas += [(_manifest, '.')]

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
        # Trim fat we never use to keep the exe smaller.  Note we do
        # NOT exclude tkinter -- the install / update GUI in
        # installer.py uses it. The 1-2MB tkinter costs is negligible
        # against the pywebview/yt-dlp base.
        'matplotlib', 'numpy', 'PIL', 'pandas', 'scipy',
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
    name='YTGrab',
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
