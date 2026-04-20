@echo off
REM YT Grab -- publish release to the public distribution repo.
REM
REM Reads version from .release-please-manifest.json, creates vX.Y.Z
REM release on github.com/SIeepyDev/YTGrab, and uploads the three
REM .exe artifacts from dist\ as release assets.
REM
REM Prereq:
REM   1. build.bat has produced dist\YTGrab.exe, dist\YTGrabUninstaller.exe,
REM      and dist\YTGrabSetup.exe.
REM   2. GH_PAT env var is set to a personal access token with `repo`
REM      scope on SIeepyDev/YTGrab.
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
    echo [release] Set a PAT with repo scope on SIeepyDev/YTGrab, e.g.:
    echo           set GH_PAT=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    pause
    goto end
)

if not exist "dist\YTGrab.exe"            goto err_nobuild
if not exist "dist\YTGrabUninstaller.exe" goto err_nobuild
if not exist "dist\YTGrabSetup.exe"       goto err_nobuild

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0release.ps1" %*
if errorlevel 1 goto err_ps

echo.
echo [release] DONE.
pause
goto end

:err_nobuild
echo.
echo [release] ERROR: dist\ is missing one of YTGrab.exe / YTGrabUninstaller.exe / YTGrabSetup.exe.
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
