@echo off
REM YT Grab -- publish release to the public distribution repo.
REM
REM Reads version from .release-please-manifest.json, creates vX.Y.Z
REM release on github.com/SIeepyDev/YT-Grab, and uploads the single
REM dist\YTGrab.exe artifact as the one release asset.
REM
REM Prereq:
REM   1. build.bat has produced dist\YTGrab.exe (the single-file
REM      installer) and dist\YTGrabUninstaller.exe (standalone
REM      uninstaller, also posted separately so users can grab it
REM      without digging into the install folder).
REM   2. GH_PAT env var is set to a personal access token with `repo`
REM      scope on SIeepyDev/YT-Grab.
REM
REM Usage:
REM   release.bat            publish current manifest version
REM   release.bat -Force     replace an existing release at that tag
REM   release.bat -Draft     publish as draft
REM   release.bat -Prerelease  mark as prerelease

setlocal
cd /d "%~dp0"

if "%GH_PAT%"=="" (
    echo.
    echo [release] ERROR: GH_PAT env var is not set.
    echo [release] Set a PAT with repo scope on SIeepyDev/YT-Grab, e.g.:
    echo           set GH_PAT=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    pause
    goto end
)

if not exist "dist\YTGrab.exe"            goto err_nobuild
if not exist "dist\YTGrabUninstaller.exe" goto err_nobuild

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0release.ps1" %*
if errorlevel 1 goto err_ps

echo.
echo [release] DONE.
pause
goto end

:err_nobuild
echo.
echo [release] ERROR: dist\YTGrab.exe or dist\YTGrabUninstaller.exe is missing.
echo [release] Run build.bat first.
pause
goto end

:err_ps
echo.
echo [release] ERROR: release.ps1 failed. See output above.
pause
goto end

:end
endlocal
