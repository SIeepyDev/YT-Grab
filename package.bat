@echo off
REM YT Grab -- packaging script.
REM
REM Produces dist\YTGrab.zip -- a shippable archive containing:
REM   1. YTGrab.exe (the PyInstaller build) -- for friends on older
REM      Windows or with Smart App Control off
REM   2. source\ subfolder with launch.bat + all .py/.html/.txt -- fallback
REM      that works on ANY Windows machine (requires Python 3.9+)
REM   3. FRIEND_README.txt explaining both paths
REM
REM Run build.bat first so dist\YTGrab.exe exists.

setlocal enableextensions enabledelayedexpansion
cd /d "%~dp0"

if not exist "dist\YTGrab.exe" goto err_noexe

REM Clean any previous package staging + zip
if exist "dist\YTGrab-pkg" rmdir /s /q "dist\YTGrab-pkg"
if exist "dist\YTGrab.zip" del /q "dist\YTGrab.zip"

echo [yt-dl pkg] Staging package contents...
mkdir "dist\YTGrab-pkg"
mkdir "dist\YTGrab-pkg\source"

REM Copy the exe to the package root
copy /y "dist\YTGrab.exe" "dist\YTGrab-pkg\YTGrab.exe" >nul

REM Bundle the uninstaller alongside the .exe so users can cleanly
REM remove app data (%LOCALAPPDATA%\YTGrab) and shortcuts in one click.
copy /y "uninstall.bat" "dist\YTGrab-pkg\uninstall.bat" >nul

REM Copy source fallback files. venv and dist are excluded (recipient
REM recreates venv on their first launch.bat run). launch.vbs is the
REM primary silent launcher; launch.bat is included as a visible fallback
REM for first-time install progress + debugging.
copy /y "launch.vbs" "dist\YTGrab-pkg\source\" >nul
copy /y "launch.bat" "dist\YTGrab-pkg\source\" >nul
copy /y "fetch_ffmpeg.bat" "dist\YTGrab-pkg\source\" >nul
copy /y "server.py" "dist\YTGrab-pkg\source\" >nul
copy /y "index.html" "dist\YTGrab-pkg\source\" >nul
copy /y "requirements.txt" "dist\YTGrab-pkg\source\" >nul
copy /y "README.md" "dist\YTGrab-pkg\source\" >nul
if exist "icon.ico" copy /y "icon.ico" "dist\YTGrab-pkg\source\" >nul
REM Also bundle the bin\ folder with ffmpeg + ffprobe already downloaded,
REM so the friend's first source-path run doesn't need the 100MB download.
if exist "bin\ffprobe.exe" (
    mkdir "dist\YTGrab-pkg\source\bin" 2>nul
    copy /y "bin\ffmpeg.exe"  "dist\YTGrab-pkg\source\bin\" >nul
    copy /y "bin\ffprobe.exe" "dist\YTGrab-pkg\source\bin\" >nul
)

REM Top-level readme for the friend
echo [yt-dl pkg] Writing FRIEND_README.txt...
(
  echo YT Grab
  echo =============
  echo.
  echo Try Option A first. If Windows blocks it, use Option B.
  echo Either way, the app runs silently: no console window, no noise.
  echo Close the browser tab to exit.
  echo.
  echo.
  echo OPTION A: Run the .exe ^(easiest^)
  echo ---------------------------------
  echo 1. Double-click YTGrab.exe
  echo 2. Your browser opens to http://localhost:8765 a second later
  echo 3. Paste a YouTube URL, pick quality/format, click Download
  echo 4. When you are done, just close the browser tab. App exits on its own.
  echo.
  echo If Windows shows "Windows protected your PC" ^(blue panel^):
  echo   Click "More info" then "Run anyway". One-time prompt.
  echo.
  echo If Windows shows "Smart App Control blocked an app" ^(no Run anyway
  echo button^): SAC is too strict to allow unsigned apps. Use Option B.
  echo.
  echo.
  echo OPTION B: Run from source ^(works everywhere^)
  echo --------------------------------------------
  echo Needs Python 3.9 or newer installed on your machine.
  echo Get it from https://www.python.org/downloads/ -- during install
  echo CHECK the "Add Python to PATH" box at the bottom of the first screen.
  echo.
  echo 1. Open the "source" folder
  echo 2. Double-click launch.vbs  ^(the silent launcher^)
  echo 3. First run shows a setup window briefly while it installs
  echo    dependencies ^(~30s^). After that, every launch is silent.
  echo 4. Your browser opens to http://localhost:8765. Same UI as the exe.
  echo 5. Close the tab to exit.
  echo.
  echo   If you want to see logs / debug, run launch.bat instead of
  echo   launch.vbs -- same behavior but with a visible console.
  echo.
  echo.
  echo Where do downloads go?
  echo ----------------------
  echo Next to whichever file you launched:
  echo   .exe mode:    same folder as YTGrab.exe  ^(in a "downloads" subfolder^)
  echo   source mode:  source\downloads\
  echo.
  echo.
  echo Stopping it
  echo -----------
  echo Just close the browser tab. Server auto-shuts-down within a minute.
  echo.
  echo.
  echo Uninstalling
  echo ------------
  echo Run uninstall.bat to wipe app data ^(%%LOCALAPPDATA%%\YTGrab^) and
  echo shortcuts. Your downloads folder is left alone -- back it up or
  echo delete it yourself if you're done with the videos too.
  echo.
) > "dist\YTGrab-pkg\FRIEND_README.txt"

REM Zip the whole package using PowerShell's Compress-Archive -- every
REM modern Windows has this built in, no extra tools needed.
echo [yt-dl pkg] Creating zip...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\YTGrab-pkg\*' -DestinationPath 'dist\YTGrab.zip' -Force"
if errorlevel 1 goto err_zip

REM Keep the staging folder around for inspection, but the main deliverable
REM is the .zip. Advise on size.
for %%A in ("dist\YTGrab.zip") do set PKGSIZE=%%~zA
set /a PKGSIZE_MB=%PKGSIZE% / 1048576

echo.
echo ==================================================
echo [yt-dl pkg] DONE
echo.
echo   Your zip:  %cd%\dist\YTGrab.zip  ^(~%PKGSIZE_MB% MB^)
echo.
echo   Send this one file. It contains the .exe for friends
echo   who can run it, AND a source fallback for friends who
echo   get blocked by Smart App Control or just prefer it.
echo ==================================================

start "" explorer "%cd%\dist"
pause
goto end

:err_noexe
echo.
echo [yt-dl pkg] ERROR: dist\YTGrab.exe not found.
echo [yt-dl pkg] Run build.bat first, then re-run package.bat.
pause
goto end

:err_zip
echo.
echo [yt-dl pkg] ERROR: zip step failed. Check that PowerShell can run
echo [yt-dl pkg] Compress-Archive ^(try: Get-Command Compress-Archive in PS^).
pause
goto end

:end
endlocal
