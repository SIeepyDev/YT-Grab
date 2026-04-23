@echo off
REM YT Grab -- packaging script.
REM
REM Produces dist\YTGrab.zip -- a shippable archive containing:
REM   1. YTGrab.exe (the single-file installer that bundles the app +
REM      uninstaller) -- for friends on older Windows or with Smart
REM      App Control off
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

REM Copy the single installer exe to the package root. It bundles the
REM real app AND the standalone uninstaller as data resources, so the
REM friend gets both after running it once.
copy /y "dist\YTGrab.exe" "dist\YTGrab-pkg\YTGrab.exe" >nul

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
  echo 2. A small setup window appears for a few seconds while it
  echo    installs itself to %%LOCALAPPDATA%%\Programs\YTGrab and
  echo    creates Desktop + Start Menu shortcuts. One-time only.
  echo 3. The app opens automatically. Paste a YouTube URL, pick
  echo    quality/format, click Download.
  echo 4. When you are done, just close the window. App exits on its own.
  echo.
  echo After the first run, launch YT Grab from the new Desktop or
  echo Start Menu shortcut -- you do not need to keep this zip.
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
  echo   .exe mode:    %%LOCALAPPDATA%%\Programs\YTGrab\downloads
  echo   source mode:  source\downloads\
  echo.
  echo.
  echo Stopping it
  echo -----------
  echo Just close the window. Server auto-shuts-down within a minute.
  echo.
  echo.
  echo Uninstalling
  echo ------------
  echo Use the "Uninstall YT Grab" shortcut ^(Desktop or Start Menu^)
  echo that was created during the first run. It will:
  echo   - optionally export your downloads and history to your Desktop
  echo   - stop YT Grab if it's running
  echo   - delete %%LOCALAPPDATA%%\YTGrab ^(webview cache^)
  echo   - delete the Desktop + Start Menu shortcuts
  echo   - delete the install folder when it closes
  echo.
  echo It does NOT touch the registry, ProgramData, or any other folder.
  echo.
) > "dist\YTGrab-pkg\FRIEND_README.txt"

REM Zip the whole package using .NET's built-in System.IO.Compression.ZipFile.
REM We used to rely on PowerShell's Compress-Archive cmdlet, but on some
REM machines the Microsoft.PowerShell.Archive module fails to load
REM ("command was found in the module ... but the module could not be
REM loaded"). The .NET API ships with every Windows install since Win8
REM and has no such breakage.
echo [yt-dl pkg] Creating zip...
powershell -NoProfile -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; if (Test-Path 'dist\YTGrab.zip') { Remove-Item 'dist\YTGrab.zip' -Force }; [System.IO.Compression.ZipFile]::CreateFromDirectory((Resolve-Path 'dist\YTGrab-pkg').Path, (Join-Path (Resolve-Path 'dist').Path 'YTGrab.zip'))"
if errorlevel 1 goto err_zip
if not exist "dist\YTGrab.zip" goto err_zip

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
echo [yt-dl pkg] ERROR: zip step failed. .NET ZipFile returned a non-zero
echo [yt-dl pkg] exit code. Make sure PowerShell can load
echo [yt-dl pkg] System.IO.Compression.FileSystem ^(ships with every
echo [yt-dl pkg] Windows since Win8^).
pause
goto end

:end
endlocal
