@echo off
REM ============================================================
REM YT Grab - Uninstaller
REM ------------------------------------------------------------
REM Removes everything YT Grab writes to a user's system:
REM   - Running YTGrab.exe process
REM   - %LOCALAPPDATA%\YTGrab\ (WebView2 cache, cookies)
REM   - Desktop + Start Menu shortcuts (current + legacy names)
REM
REM Does NOT touch:
REM   - The install folder itself (this script lives in it)
REM   - downloads\ / previous_downloads\ (your videos)
REM   - history.json / activity.json (your history)
REM
REM After this finishes, you can delete the install folder
REM manually if you want a full wipe. Keep downloads\ first
REM if there's anything you want to save.
REM ============================================================

setlocal
echo.
echo ============================================
echo  YT Grab - Uninstaller
echo ============================================
echo.
echo This will remove:
echo   - YTGrab.exe process (if running)
echo   - %%LOCALAPPDATA%%\YTGrab\
echo   - Desktop shortcut (YT Grab.lnk)
echo   - Start Menu shortcut (YT Grab.lnk)
echo   - Legacy shortcuts (YT Downloader.lnk)
echo.
echo It will NOT touch your downloads folder or
echo this install folder. You can delete those
echo manually when you're ready.
echo.
set /p CONFIRM=Continue? (y/N):
if /i not "%CONFIRM%"=="y" (
    echo.
    echo Cancelled. Nothing was removed.
    pause
    exit /b 0
)

echo.
echo [1/4] Stopping YTGrab.exe if running...
taskkill /F /IM YTGrab.exe >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo       killed.
) else (
    echo       not running.
)

echo.
echo [2/4] Removing %%LOCALAPPDATA%%\YTGrab...
if exist "%LOCALAPPDATA%\YTGrab" (
    rmdir /S /Q "%LOCALAPPDATA%\YTGrab"
    if exist "%LOCALAPPDATA%\YTGrab" (
        echo       FAILED - some files locked. Close YT Grab and retry.
    ) else (
        echo       removed.
    )
) else (
    echo       already gone.
)

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

echo.
echo [4/4] Done.
echo.
echo ============================================
echo  Uninstall complete.
echo ============================================
echo.
echo To fully remove YT Grab, delete this folder:
echo   "%~dp0"
echo.
echo Back up "downloads" first if you want to
echo keep any of your videos.
echo.
endlocal
pause
