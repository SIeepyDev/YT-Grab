@echo off
REM YT Grab -- build script.
REM Produces dist\YTGrab.exe (a standalone Windows binary your friend
REM can double-click -- no Python install required on the target machine).
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
echo [yt-dl build] Running PyInstaller. This takes 30-90 seconds the first time.
venv\Scripts\python.exe -m PyInstaller --clean YTGrab.spec
if errorlevel 1 goto err_pyinstaller

echo.
if not exist "dist\YTGrab.exe" goto err_nooutput

echo ==================================================
echo [yt-dl build] DONE
echo.
echo   Your exe:  %cd%\dist\YTGrab.exe
echo.
echo   Send the single .exe to your friend. No Python needed on their end.
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

:err_nooutput
echo.
echo [yt-dl build] ERROR: build completed without producing dist\YTGrab.exe.
echo [yt-dl build] Check the PyInstaller output above.
pause
goto end

:end
endlocal
