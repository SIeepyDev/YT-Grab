# Commit + push v1.1 polish pass to GitHub.
# Right-click -> Run with PowerShell.

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path','User')

Write-Host ""
Write-Host "== YT Grab v1.1 commit + push ==" -ForegroundColor Cyan
Write-Host ""

git add .
git status --short

$commit = @"
v1.1: clipboard auto-detect, batch paste, playlist picker, subtitles, context menu

New features:
- Playlist support: paste a playlist URL, get a picker modal with thumbnails
  and checkboxes. Select-all / none / individual. Queues via batch pipeline.
- Subtitle (.srt) download: new "Save subtitles" checkbox. Requests both
  human-authored and auto-generated English captions in SRT format. New
  Subs combo-button on history rows.
- Right-click context menu on history rows: Copy URL, Open on YouTube,
  Reveal in folder, Rename, Redownload, Delete.
- Clipboard auto-detect: copy a YouTube URL anywhere, return to YT Grab,
  URL auto-fills with a "Enter to load" hint.
- Multi-URL batch paste: paste newline-separated YouTube URLs into the
  input and they all queue up.
- Keyboard shortcut cheatsheet (press ?), Ctrl+, opens settings.

UI polish:
- Page scroll locked: body never scrolls, only inside cards. True
  dashboard feel regardless of content volume.
- Left rail gets folder + shortcut icons alongside the gear.
- Richer empty states for History and Previous Downloads.
- Entrance stagger animation on app boot.
- Active job rows collapsed to 2 lines (title + status inline, bar below).
- Compact/Comfortable/Spacious density now drives inner row spacing,
  not just outer card padding.
- 14-color accent palette (was 6): purple, violet, indigo, blue, sky,
  cyan, teal, emerald, lime, amber, orange, red, rose, pink.
- Dark Windows title bar via DWM immersive dark mode.
- Seamless launch: window created hidden, maximized + iconed, then
  revealed in a single atomic ShowWindow call. No flicker.

Internals:
- New /api/playlist_info endpoint uses yt-dlp flat_playlist for fast
  listing (1 sec for 50 videos vs 30 sec with per-video probe).
- Rename + sidecar deletion now handle subtitle_path.
- Shortcut migration: old "YT Downloader.lnk" auto-deleted on launch.
"@

git commit -m $commit | Out-Host
Write-Host ""
Write-Host "Pushing to origin main..." -ForegroundColor Cyan
git push

Write-Host ""
Write-Host "Done. View on GitHub:" -ForegroundColor Green
Write-Host "  https://github.com/SIeepyDev/yt-grab"
Write-Host ""
pause
