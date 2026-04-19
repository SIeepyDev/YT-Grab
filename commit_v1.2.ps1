# Commit + push v1.2 theming polish to GitHub.
# Right-click -> Run with PowerShell.

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path','User')

Write-Host ""
Write-Host "== YT Grab v1.2 commit + push ==" -ForegroundColor Cyan
Write-Host ""

git add .
git status --short

$commit = @"
v1.2: themebar grows up - layout/background/gradient sections, system mode, OKLCH palette

Themebar (right-side panel):
- Theme mode picker now has 3 cards: Light / Dark / System.
  System listens to prefers-color-scheme and follows the OS live -
  flip Windows' theme and the app flips with it, no restart.
- Mode switching is animated: 380ms cubic-bezier cross-fade on bg,
  text, borders, and accent surfaces. Respects prefers-reduced-motion.

Layout section (new):
- Three layout presets: Balanced (default 3-col), Focus (middle wider,
  sides slimmer), Stacked (single-column).
- Per-column visibility toggles for History / Middle / Queue.

Background section (new):
- Seven base backgrounds across both modes:
    Dark:  Void, Graphite, Midnight, Slate.
    Light: Cream, Paper, Snow.
- Optional subtle film-grain noise overlay.

Gradient section (new):
- Six gradient presets: Aurora, Sunset, Ocean, Plum, Ember, plus
  Accent-derived (auto-mesh from current accent color).
- Intensity slider (0-100%) and optional slow ~40s orbit animation.

Color picker:
- Inner SV disc is now true Liquid Glass: transparent, fitted within
  the hue ring, frosted with backdrop-filter blur(18px) saturate(1.4).
  Feels like Apple's material instead of a solid JS-painted gradient.

Internals:
- Accent palette derives from a single hex via color-mix(in oklch, ...).
  --accent, --accent-hover, --accent-subtle, --accent-text, and
  --accent-contrast stay perceptually consistent across the full
  color range. No more muddy hovers on amber/lime.
- Auto-contrast text: buttons and badges on accent backgrounds pick
  black or white based on Rec. 709 luminance (threshold 0.55).
  Pastel accents flip to dark text, dark accents keep white.
- Every themebar control is a single data-* attribute on <html>.
  CSS does the work, JS just toggles attributes. Fast and declarative.
- Body now establishes a stacking context (position: relative; z-index: 0)
  so background/gradient pseudo-elements sit between bg and content
  without tinting card fills.
- New persisted settings: layoutPreset, colsVisible, bgPreset, bgNoise,
  gradientPreset, gradientIntensity, gradientAnimate.
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
