@echo off
REM YT Grab -- build script.
REM
REM Produces three files in dist\. TWO are release assets, one is an
REM intermediate that lives inside the bundled installer:
REM
REM   dist\YTGrabApp.exe           the real Flask + pywebview app.
REM                                NOT a release asset; bundled inside
REM                                dist\YTGrab.exe below.
REM
REM   dist\YTGrabUninstaller.exe   standalone tkinter uninstaller.
REM                                Also a release asset -- posted on its
REM                                own as a safety-net download for users
REM                                who lost their uninstaller shortcut.
REM                                ALSO bundled inside YTGrab.exe so a
REM                                normal install still wires up the
REM                                "Uninstall YT Grab" shortcut.
REM
REM   dist\YTGrab.exe              the main public download. Contains
REM                                the two binaries above as PyInstaller
REM                                data resources; handles install and
REM                                auto-update.
REM
REM Prereq: launch.bat has been run at least once so the venv exists.

setlocal
cd /d "%~dp0"

REM The build uses the same venv as the running server. If someone's never
REM launched the app, we need the venv first.
if not exist "venv\Scripts\python.exe" goto err_novenv

echo.
echo [yt-dl build] Installing build deps into venv: pyinstaller...
venv\Scripts\python.exe -m pip install -r build_requirements.txt
if errorlevel 1 goto err_pip

echo [yt-dl build] Making sure app runtime deps are current: flask, yt-dlp, imageio-ffmpeg, youtube-transcript-api...
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto err_pip

REM Wipe any previous build output so we don't ship a stale binary.
if exist "build" rmdir /s /q "build"
if exist "dist"  rmdir /s /q "dist"

REM Convert icon.png -> icon.ico if the PNG is present. Silently skips
REM if there's no icon.png. The PyInstaller spec picks up icon.ico
REM automatically when it exists.
echo [yt-dl build] Checking for custom icon...
venv\Scripts\python.exe build_icon.py
if errorlevel 1 goto err_icon

REM Ensure bin\ffmpeg.exe + bin\ffprobe.exe are available so PyInstaller
REM can bundle them into the exe. fetch_ffmpeg.bat is idempotent.
echo [yt-dl build] Checking for bundled ffmpeg + ffprobe...
if not exist "bin\ffprobe.exe" call fetch_ffmpeg.bat
if not exist "bin\ffprobe.exe" goto err_ffmpeg

echo.
echo [yt-dl build] Building YTGrabApp.exe (inner app). This takes 30-90 seconds the first time.
venv\Scripts\python.exe -m PyInstaller --clean YTGrab.spec
if errorlevel 1 goto err_pyinstaller

echo.
if not exist "dist\YTGrabApp.exe" goto err_nooutput

REM Build the standalone uninstaller alongside the main app. Stdlib-only,
REM so this second pass adds ~10s and ~10MB. Output: dist\YTGrabUninstaller.exe
echo.
echo [yt-dl build] Building YTGrabUninstaller.exe...
venv\Scripts\python.exe -m PyInstaller --clean Uninstaller.spec
if errorlevel 1 goto err_pyinstaller_uninst
if not exist "dist\YTGrabUninstaller.exe" goto err_nouninst

REM Build the single public-facing YTGrab.exe. It bundles the two inner
REM binaries above as PyInstaller data resources and handles install +
REM auto-update on the user's machine. Installer.spec reads dist\YTGrabApp.exe
REM and dist\YTGrabUninstaller.exe off disk at build time, so the two
REM passes above MUST succeed first.
echo.
echo [yt-dl build] Building YTGrab.exe (single public download)...
venv\Scripts\python.exe -m PyInstaller --clean Installer.spec
if errorlevel 1 goto err_pyinstaller_setup
if not exist "dist\YTGrab.exe" goto err_nosetup

echo ==================================================
echo [yt-dl build] DONE
echo.
echo   Ships to GitHub:  %cd%\dist\YTGrab.exe             (main download)
echo   Ships to GitHub:  %cd%\dist\YTGrabUninstaller.exe  (safety-net)
echo   (bundled inside)  %cd%\dist\YTGrabApp.exe          (inside YTGrab.exe)
echo.
echo   Run release.bat to upload the two release assets. YTGrabApp.exe
echo   stays local -- it rides inside YTGrab.exe.
echo   First launch may take 5-10 seconds as Windows unpacks it.
echo   Windows Defender may warn -- click "More info" then "Run anyway".
echo ==================================================

REM Open Explorer on the dist folder so the exe is right there.
start "" explorer "%cd%\dist"
pause
goto end

:err_novenv
echo.
echo [yt-dl build] ERROR: venv is missing. Run launch.bat first to create it.
pause
goto end

:err_pip
echo.
echo [yt-dl build] ERROR: pip install failed. Check your internet connection.
pause
goto end

:err_icon
echo.
echo [yt-dl build] ERROR: icon conversion failed. Check icon.png is a valid PNG.
pause
goto end

:err_ffmpeg
echo.
echo [yt-dl build] ERROR: could not fetch ffmpeg + ffprobe into bin\.
echo [yt-dl build] Check your internet connection and retry.
pause
goto end

:err_pyinstaller
echo.
echo [yt-dl build] ERROR: PyInstaller failed. Scroll up for the real error.
pause
goto end

:err_pyinstaller_uninst
echo.
echo [yt-dl build] ERROR: PyInstaller failed on Uninstaller.spec.
echo [yt-dl build] Scroll up for the real error.
pause
goto end

:err_nooutput
echo.
echo [yt-dl build] ERROR: build completed without producing dist\YTGrabApp.exe.
echo [yt-dl build] Check the PyInstaller output above.
pause
goto end

:err_nouninst
echo.
echo [yt-dl build] ERROR: build completed without producing dist\YTGrabUninstaller.exe.
echo [yt-dl build] Check the PyInstaller output above for the uninstaller pass.
pause
goto end

:err_pyinstaller_setup
echo.
echo [yt-dl build] ERROR: PyInstaller failed on Installer.spec.
echo [yt-dl build] Scroll up for the real error.
pause
goto end

:err_nosetup
echo.
echo [yt-dl build] ERROR: build completed without producing dist\YTGrab.exe.
echo [yt-dl build] Check the PyInstaller output above for the bundler pass.
pause
goto end

:end
endlocal
