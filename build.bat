@echo off
REM YT Grab -- build script.
REM
REM Produces the two release assets in dist\:
REM
REM   dist\YTGrab.exe              the app. Single file, handles its
REM                                own first-run install + self-update.
REM
REM   dist\YTGrabUninstaller.exe   standalone tkinter uninstaller. Fetched
REM                                from the GitHub release by YTGrab.exe
REM                                on first install; also posted as its
REM                                own release asset so users can grab it
REM                                directly if they ever need to.
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

REM Build order matters: YTGrab.spec bundles dist\YTGrabUninstaller.exe
REM as a PyInstaller data resource so the install flow can extract the
REM uninstaller locally instead of downloading from GitHub. That means
REM YTGrabUninstaller.exe MUST exist before YTGrab.spec runs.
echo.
echo [yt-dl build] Building YTGrabUninstaller.exe (bundled into YTGrab.exe below)...
venv\Scripts\python.exe -m PyInstaller --clean Uninstaller.spec
if errorlevel 1 goto err_pyinstaller_uninst
if not exist "dist\YTGrabUninstaller.exe" goto err_nouninst

echo.
echo [yt-dl build] Building YTGrab.exe. This takes 30-90 seconds the first time.
venv\Scripts\python.exe -m PyInstaller --clean YTGrab.spec
if errorlevel 1 goto err_pyinstaller
if not exist "dist\YTGrab.exe" goto err_nooutput

echo ==================================================
echo [yt-dl build] DONE
echo.
echo   Release asset:   %cd%\dist\YTGrab.exe
echo   Release asset:   %cd%\dist\YTGrabUninstaller.exe
echo.
echo   Run release.bat to upload both files to the latest GitHub release.
echo   First launch of YTGrab.exe may take 5-10 seconds as Windows unpacks it.
echo   Windows Defender may warn -- click "More info" then "Run anyway".
echo ==================================================

REM Open Explorer on the dist folder so the exes are right there.
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
echo [yt-dl build] ERROR: PyInstaller failed on YTGrab.spec. Scroll up for the real error.
pause
goto end

:err_pyinstaller_uninst
echo.
echo [yt-dl build] ERROR: PyInstaller failed on Uninstaller.spec. Scroll up for the real error.
pause
goto end

:err_nooutput
echo.
echo [yt-dl build] ERROR: build completed without producing dist\YTGrab.exe.
echo [yt-dl build] Check the PyInstaller output above.
pause
goto end

:err_nouninst
echo.
echo [yt-dl build] ERROR: build completed without producing dist\YTGrabUninstaller.exe.
echo [yt-dl build] Check the PyInstaller output above for the uninstaller pass.
pause
goto end

:end
endlocal
