@echo off
REM YT Grab -- factory reset.
REM Removes venv, build artifacts, bundled exe, package zip, and Python
REM pycache. Does NOT touch your downloads/ folder or history.json.
REM
REM Run this when you want to rebuild everything from scratch, or just
REM to clean up old .exe / .zip artifacts before shipping a new one.

title YT Grab - Clean
setlocal
cd /d "%~dp0"

echo.
echo [yt-dl clean] This will delete:
echo     venv\                  ^(Python virtual environment^)
echo     build\                 ^(PyInstaller staging^)
echo     dist\                  ^(built exe + zipped package^)
echo     __pycache__\           ^(Python bytecode cache^)
echo     icon.ico               ^(only if icon.png exists -- it will regenerate^)
echo.
echo [yt-dl clean] Your downloads\, history.json, and bin\ffmpeg stay SAFE
echo [yt-dl clean] ^(bin\ takes 100MB to re-download; keeping it saves time^).
echo.
choice /c YN /n /m "Proceed? [Y/N] "
if errorlevel 2 goto cancelled

echo.
echo [yt-dl clean] Wiping...
if exist "venv"          rmdir /s /q "venv"
if exist "build"         rmdir /s /q "build"
if exist "dist"          rmdir /s /q "dist"
if exist "__pycache__"   rmdir /s /q "__pycache__"
REM Only wipe icon.ico if icon.png is around to regenerate it
if exist "icon.png" if exist "icon.ico" del /q "icon.ico"

echo.
echo [yt-dl clean] DONE. Run launch.bat / launch.vbs to set everything up fresh.
pause
goto end

:cancelled
echo.
echo [yt-dl clean] Cancelled. Nothing deleted.
pause
goto end

:end
endlocal
