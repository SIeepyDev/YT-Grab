# Commit + push v1.2.1 layout fix to GitHub.
# Right-click -> Run with PowerShell.

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path','User')

Write-Host ""
Write-Host "== YT Grab v1.2.1 commit + push ==" -ForegroundColor Cyan
Write-Host ""

git add .
git status --short

$commit = @"
v1.2.1: layout preset fix - Focus is dramatic, Stacked actually stacks

Focus layout:
- Was: minmax(0, 0.75fr) / 1.8fr / minmax(0, 0.75fr)
  On a 1920px window this only shrinks sides ~150px each - barely
  distinguishable from Classic.
- Now: 280px / 1fr / 280px. Sides become fixed slim rails, middle
  column takes ALL remaining width. On 1920px that's a middle of
  ~1260px vs classic ~750px - unmistakable.

Stacked layout:
- Was: grid-template-columns: 1fr. This just stretched column 1
  (History) across the full 1480px page-grid width while columns
  2 and 3 sat below it looking lost.
- Now: single column, max-width 820px, margin auto. True centered
  single-column view.
- grid-template-areas reorders children so URL paste lands at the
  top, History next, Active at bottom. Matches the natural user
  flow (paste -> browse -> download progress).
- Last-card flex-stretch disabled in stacked mode - tall cards
  cap at 360px internal scroll height instead of each trying to
  fill the whole page.
- Body scroll-lock unlocked when data-layout=stacked, since the
  stacked column is usually taller than 100vh. Other layouts keep
  the v1.1 hard viewport lock.
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
