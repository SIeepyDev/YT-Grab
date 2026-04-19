@echo off
REM YT Grab -- launch script.
REM Double-click to start. First run installs dependencies; subsequent runs
REM just start the server. Opens the UI in your browser once listening.

title YT Grab
setlocal
cd /d "%~dp0"

REM Skip install if venv already exists.
if exist "venv\Scripts\python.exe" goto after_install

echo.
echo [yt-dl] First-time setup: creating virtual environment...
python -m venv venv
if errorlevel 1 goto err_venv

echo [yt-dl] Installing dependencies: flask, yt-dlp, youtube-transcript-api...
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto err_pip

:after_install

echo.
echo [yt-dl] Checking for yt-dlp updates...
venv\Scripts\python.exe -m pip install --upgrade yt-dlp >nul 2>&1

REM Regenerate icon.ico from icon.png if the ico is missing. clean.bat
REM deletes the ico (correctly -- it's a derived artifact), but launch
REM needs it for the Chrome app-mode window icon and browser favicon.
if not exist "icon.png"  goto after_icon
if exist "icon.ico"      goto after_icon
echo [yt-dl] Generating icon.ico from icon.png...
venv\Scripts\python.exe -m pip install pillow >nul 2>&1
venv\Scripts\python.exe build_icon.py
:after_icon

REM NOTE: ffmpeg + ffprobe are OPTIONAL. Downloads work without them;
REM you just don't get embedded thumbnails / metadata tags. If you want
REM that full-premium polish, run fetch_ffmpeg.bat manually (one-time
REM ~130MB download). build.bat runs it automatically before PyInstaller
REM so the bundled exe always ships with ffmpeg inside.

REM Kill any stale instance holding port 8765 (previous run didn't shut
REM down cleanly). The second-instance-guard in server.py handles this
REM cleanly at the Python level too, but nuking a stuck process here is
REM belt + suspenders.
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8765 " ^| findstr "LISTENING"') do taskkill /f /pid %%a >nul 2>&1

REM Spawn the server as pythonw.exe (no-console Python) detached from
REM this cmd window, then exit. The server itself opens the browser in
REM chromeless app-mode window once it's listening -- no need for us
REM to race it with a `start http://...` here.
echo [yt-dl] Starting server on http://localhost:8765
echo [yt-dl] Close the app window to stop.
start "" /b venv\Scripts\pythonw.exe server.py
goto end

:err_venv
echo.
echo [yt-dl] ERROR: could not create venv. Make sure Python 3.9+ is installed and on PATH.
echo [yt-dl] Test from a new terminal: python --version
pause
goto end

:err_pip
echo.
echo [yt-dl] ERROR: dependency install failed. Check your internet connection and retry.
pause
goto end

:end
endlocal
