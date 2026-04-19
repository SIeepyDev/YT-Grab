@echo off
REM YT Grab -- fetch ffmpeg + ffprobe.
REM
REM Downloads yt-dlp's official ffmpeg build (maintained by the yt-dlp
REM team, includes both ffmpeg.exe and ffprobe.exe) and places just
REM those two binaries into bin\ next to the app. Idempotent: skips
REM the download if bin\ffprobe.exe already exists.
REM
REM Uses curl.exe (built into Windows 10+ since 2017) instead of
REM PowerShell's Invoke-WebRequest -- curl is 3-5x faster and shows
REM a real progress bar with bytes-downloaded + ETA.
REM
REM Called by:
REM   * build.bat  (before PyInstaller, so the exe bundles the binaries)
REM   * manually, when you want metadata/thumbnail embedding locally

title YT Grab - Fetch ffmpeg
setlocal
cd /d "%~dp0"

if exist "bin\ffmpeg.exe" if exist "bin\ffprobe.exe" (
    echo [fetch_ffmpeg] bin\ffmpeg.exe + bin\ffprobe.exe already present.
    goto end
)

echo.
echo [fetch_ffmpeg] Downloading ffmpeg + ffprobe ^(~130MB one-time^)
echo [fetch_ffmpeg] Source: github.com/yt-dlp/FFmpeg-Builds
echo.

if not exist "bin" mkdir "bin"

REM curl.exe ships with Windows 10+ since build 17063 (2017). -L follows
REM redirects (GitHub releases redirect to S3/CDN); --progress-bar shows
REM a simple progress bar; --fail makes it return non-zero on HTTP errors
REM instead of silently saving the error page.
where curl.exe >nul 2>&1
if errorlevel 1 goto err_no_curl

curl.exe -L --fail --progress-bar -o "ffmpeg_dl.zip" "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
if errorlevel 1 goto err_download
echo.

echo [fetch_ffmpeg] Extracting...
REM tar.exe also ships with Windows 10+ and handles .zip natively. Faster
REM than PowerShell's Expand-Archive for this size.
if exist "ffmpeg_extract" rmdir /s /q "ffmpeg_extract"
mkdir "ffmpeg_extract"
tar.exe -xf "ffmpeg_dl.zip" -C "ffmpeg_extract"
if errorlevel 1 goto err_extract

REM The archive nests the binaries under ffmpeg-master-.../bin/. Find
REM them recursively rather than hardcoding the folder name (yt-dlp
REM occasionally changes the naming).
for /r "ffmpeg_extract" %%f in (ffmpeg.exe)  do copy /y "%%f" "bin\ffmpeg.exe"  >nul
for /r "ffmpeg_extract" %%f in (ffprobe.exe) do copy /y "%%f" "bin\ffprobe.exe" >nul

if not exist "bin\ffmpeg.exe"  goto err_extract
if not exist "bin\ffprobe.exe" goto err_extract

REM Clean up the zip + extraction folder; we only wanted the two exes.
rmdir /s /q "ffmpeg_extract"
del /q "ffmpeg_dl.zip"

echo.
echo [fetch_ffmpeg] DONE. bin\ffmpeg.exe and bin\ffprobe.exe are ready.
echo [fetch_ffmpeg] Metadata + thumbnail embedding will now work.
goto end

:err_no_curl
echo.
echo [fetch_ffmpeg] ERROR: curl.exe not found. Needs Windows 10 build 17063 or newer.
echo [fetch_ffmpeg] Alternative: install ffmpeg via  winget install Gyan.FFmpeg
pause
goto end

:err_download
echo.
echo [fetch_ffmpeg] ERROR: download failed. Check your internet connection.
echo [fetch_ffmpeg] Alternative: install ffmpeg via  winget install Gyan.FFmpeg
pause
goto end

:err_extract
echo.
echo [fetch_ffmpeg] ERROR: extraction failed or binaries missing from archive.
echo [fetch_ffmpeg] Files in ffmpeg_extract\:
if exist "ffmpeg_extract" dir /s /b "ffmpeg_extract" | findstr /i ".exe"
pause
goto end

:end
endlocal
