@echo off
REM ============================================================
REM YT Grab - Uninstaller
REM ------------------------------------------------------------
REM Removes EVERYTHING YT Grab writes to a user's system.
REM
REM Flow:
REM   1. Optional: export your data (downloads, history, activity)
REM      to a safe folder on your Desktop before wiping
REM   2. Kill YTGrab.exe
REM   3. Delete %LOCALAPPDATA%\YTGrab\ (WebView2 data)
REM   4. Delete Desktop + Start Menu shortcuts (current + legacy)
REM   5. Self-delete the install folder this script lives in
REM
REM Doesn't touch the registry, ProgramData, or any folder
REM outside the paths above. Safe to run on any machine.
REM ============================================================

setlocal enableextensions enabledelayedexpansion
set "INSTALL_DIR=%~dp0"
REM Strip trailing backslash
if "%INSTALL_DIR:~-1%"=="\" set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"

echo.
echo ============================================
echo  YT Grab - Uninstaller
echo ============================================
echo.
echo This will completely remove YT Grab:
echo.
echo   - Stop YTGrab.exe if running
echo   - Delete %%LOCALAPPDATA%%\YTGrab\
echo   - Delete Desktop shortcut
echo   - Delete Start Menu shortcut
echo   - Delete legacy "YT Downloader" shortcuts
echo   - Delete this install folder:
echo       "%INSTALL_DIR%"
echo.
echo You can export your data ^(downloads, history^) first.
echo.

choice /C YNQ /N /M "Proceed? [Y]es / [N]o, export only / [Q]uit: "
set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE%==3 (
    echo Cancelled. Nothing was removed.
    pause
    exit /b 0
)

REM ============================================================
REM STEP 1: Export data (optional)
REM ============================================================
echo.
choice /C YN /N /M "Export downloads + history before wiping? [Y/N]: "
if %ERRORLEVEL%==1 (
    call :export_data
)

REM If user chose "N, export only" at the first prompt, stop here.
if %EXIT_CODE%==2 (
    echo.
    echo Export-only mode. Nothing else was removed.
    pause
    exit /b 0
)

REM ============================================================
REM STEP 2: Kill process
REM ============================================================
echo.
echo [1/4] Stopping YTGrab.exe if running...
taskkill /F /IM YTGrab.exe >nul 2>&1
if %ERRORLEVEL%==0 (
    echo       killed.
    REM Give Windows a second to release file handles
    timeout /t 2 /nobreak >nul
) else (
    echo       not running.
)

REM ============================================================
REM STEP 3: App data
REM ============================================================
echo.
echo [2/4] Removing %%LOCALAPPDATA%%\YTGrab...
if exist "%LOCALAPPDATA%\YTGrab" (
    rmdir /S /Q "%LOCALAPPDATA%\YTGrab" 2>nul
    if exist "%LOCALAPPDATA%\YTGrab" (
        echo       FAILED - files locked. Close YT Grab fully and retry.
        pause
        exit /b 1
    )
    echo       removed.
) else (
    echo       already gone.
)

REM ============================================================
REM STEP 4: Shortcuts
REM ============================================================
echo.
echo [3/4] Removing shortcuts...
set "SM=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
set "DK=%USERPROFILE%\Desktop"
for %%F in (
    "%SM%\YT Grab.lnk"
    "%DK%\YT Grab.lnk"
    "%SM%\YT Downloader.lnk"
    "%DK%\YT Downloader.lnk"
) do (
    if exist %%F (
        del /F /Q %%F >nul 2>&1
        echo       removed %%~nxF
    )
)

REM ============================================================
REM STEP 5: Self-delete install folder
REM ============================================================
echo.
echo [4/4] Removing install folder...
echo       "%INSTALL_DIR%"
echo.
echo YT Grab is now fully uninstalled.
if defined EXPORT_TARGET (
    echo Your exported data is at:
    echo   "%EXPORT_TARGET%"
    echo.
)
echo This window will close and the install folder
echo will be deleted in 3 seconds.
echo.

REM Spawn a detached cmd that waits for this script to exit,
REM then wipes the install folder. Classic self-delete pattern.
start "" /b cmd /c "timeout /t 3 /nobreak >nul & rmdir /s /q ""%INSTALL_DIR%"""

endlocal
exit /b 0


REM ============================================================
REM :export_data
REM Copies downloads\, previous_downloads\, history.json,
REM activity.json to Desktop\YTGrab-export-YYYY-MM-DD_HHMMSS\
REM ============================================================
:export_data
    REM Build a timestamped folder name: YTGrab-export-2026-04-19_214530.
    REM Uses PowerShell because wmic is removed on Windows 11 24H2+ and
    REM the %DATE%/%TIME% locale formats are unreliable across machines.
    for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set "DT=%%I"
    if "%DT%"=="" set "DT=export"
    set "EXPORT_TARGET=%USERPROFILE%\Desktop\YTGrab-export-%DT%"

    echo.
    echo Exporting to: "%EXPORT_TARGET%"
    mkdir "%EXPORT_TARGET%" 2>nul

    if exist "%INSTALL_DIR%\downloads" (
        echo   copying downloads\...
        xcopy /E /I /Q /Y "%INSTALL_DIR%\downloads" "%EXPORT_TARGET%\downloads" >nul
    )
    if exist "%INSTALL_DIR%\previous_downloads" (
        echo   copying previous_downloads\...
        xcopy /E /I /Q /Y "%INSTALL_DIR%\previous_downloads" "%EXPORT_TARGET%\previous_downloads" >nul
    )
    if exist "%INSTALL_DIR%\history.json" (
        echo   copying history.json...
        copy /Y "%INSTALL_DIR%\history.json" "%EXPORT_TARGET%\history.json" >nul
    )
    if exist "%INSTALL_DIR%\activity.json" (
        echo   copying activity.json...
        copy /Y "%INSTALL_DIR%\activity.json" "%EXPORT_TARGET%\activity.json" >nul
    )
    echo   done.
exit /b 0
