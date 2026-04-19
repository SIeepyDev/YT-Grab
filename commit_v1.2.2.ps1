# Commit + push v1.2.2 themebar polish to GitHub.
# Right-click -> Run with PowerShell.

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path','User')

Write-Host ""
Write-Host "== YT Grab v1.2.2 commit + push ==" -ForegroundColor Cyan
Write-Host ""

git add .
git status --short

$commit = @"
v1.2.2: drop Layout section, polish Background and Gradient

Layout section removed entirely:
- Classic was the winner. Focus was too subtle, Stacked never felt
  right on wide monitors. Killing choice here removes 3 preset cards,
  3 column-visibility chips, 2 settings keys (layoutPreset, colsVisible),
  4 JS functions, ~50 lines of CSS. Page grid is back to the clean
  3-col Classic default, no branching.

Background section:
- Was: 7 unlabeled color circles in a dense 7-column row. Titles
  only visible on hover. No way to tell what Graphite vs Slate was
  without mousing over each one.
- Now: labeled preset-cards (same primitive as Gradient) so every
  option has a name underneath. 4-column grid, bigger tap targets.
- Filtered by theme mode: Void/Graphite/Midnight/Slate show in Dark
  mode, Cream/Snow show in Light mode. Default shows in both with
  the correct bg for the active theme. You only see relevant options.
- Removed Paper (redundant with Cream/Snow). 7 -> 8 named bgs (5 dark,
  3 light).
- Film grain now has a hint line underneath: "Subtle analog noise
  overlay on the canvas. Adds depth without affecting contrast."

Gradient section:
- Intensity readout now shows % suffix (40%, 80%, ...) so it's clear
  the slider maps to a percent.
- Intensity hint line: "How visible the mesh is. 0% hides it, 100%
  is full-bleed."
- Animated drift hint line: "Slow ~40s orbit - colors drift gently
  like a lava lamp. Off by default." Explains what the toggle does.
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
