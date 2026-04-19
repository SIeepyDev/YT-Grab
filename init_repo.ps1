# ----------------------------------------------------------------
# YT Grab -- one-click repo initializer.
#
# What this does:
#   1. Removes any partial .git folder from when the repo was staged
#   2. Runs git init, adds all files, creates the first commit
#   3. Optionally creates the GitHub repo via gh CLI (or prints the
#      manual remote-add + push commands if gh isn't installed)
#
# Usage:
#   Right-click this file -> Run with PowerShell
#   OR from a terminal:  powershell -ExecutionPolicy Bypass -File .\init_repo.ps1
# ----------------------------------------------------------------

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

Write-Host ""
Write-Host "== YT Grab repo initializer ==" -ForegroundColor Cyan
Write-Host ""

# --- 1. Wipe any partial .git state from the staging sandbox ---
if (Test-Path .git) {
    Write-Host "Cleaning partial .git from staging..."
    Remove-Item -Recurse -Force .git
}

# --- 2. Fresh git init + first commit ---
Write-Host "Initializing git repo..."
git init --initial-branch=main | Out-Null
git add . | Out-Null

$commitMsg = @"
Initial commit: YT Grab v1.0 (standalone beta)

Local-first YouTube downloader for Windows.
- Native pywebview + WebView2 window, dark title bar
- 3-column layout (History | URL+Preview+Previous | Active)
- Every format up to 4K VP9, lossless audio options
- Rename from UI, Recycle-Bin delete with Ctrl+Z undo
- 14-color accent palette + UI density settings (persisted)
- Auto-created Start Menu + Desktop shortcut, own taskbar identity
- PyInstaller single-file exe build + source fallback zip
"@

git commit -m $commitMsg | Out-Null
Write-Host "  Committed: $(git rev-parse --short HEAD)" -ForegroundColor Green
Write-Host ""

# --- 3. Push to GitHub ---
$ghAvailable = $null -ne (Get-Command gh -ErrorAction SilentlyContinue)

if ($ghAvailable) {
    Write-Host "GitHub CLI detected. Creating private repo + pushing..."
    try {
        gh repo create yt-grab `
          --private `
          --source=. `
          --remote=origin `
          --push `
          --description "Local-first YouTube downloader for Windows. Native UI, no telemetry."
        Write-Host ""
        Write-Host "Done. Repo is live." -ForegroundColor Green
        Write-Host "Opening in browser..."
        gh repo view --web
    } catch {
        Write-Host "gh repo create failed: $_" -ForegroundColor Red
        Write-Host "Fall back to the manual steps below." -ForegroundColor Yellow
        $ghAvailable = $false
    }
}

if (-not $ghAvailable) {
    Write-Host "GitHub CLI (gh) not installed -- doing the init part only."
    Write-Host ""
    Write-Host "To finish the push yourself:" -ForegroundColor Cyan
    Write-Host "  1. Go to https://github.com/new"
    Write-Host "  2. Name: yt-grab, Visibility: Private, DON'T init with README/LICENSE/gitignore"
    Write-Host "  3. Create the repo, then run in this folder:"
    Write-Host ""
    Write-Host "     git remote add origin https://github.com/SIeepyDev/yt-grab.git" -ForegroundColor White
    Write-Host "     git push -u origin main" -ForegroundColor White
    Write-Host ""
    Write-Host "Or install gh CLI (https://cli.github.com) and re-run this script."
}

Write-Host ""
Write-Host "Repo staged at: $PSScriptRoot"
Write-Host ""
pause
