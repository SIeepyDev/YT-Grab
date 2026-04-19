# Commit + push v1.3 typography + glass pass to GitHub.
# Right-click -> Run with PowerShell.

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path','User')

Write-Host ""
Write-Host "== YT Grab v1.3 commit + push ==" -ForegroundColor Cyan
Write-Host ""

git add .
git status --short

$commit = @"
v1.3: Typography + Glass - text controls and frosted cards

Typography (new themebar section):
- Size slider: 85-120% of 14px body base. Maps to --font-scale
  CSS token; body font-size = calc(14px * var(--font-scale)), so
  every inheriting element scales together.
- Line slider: 1.30-1.70 line-height. Stored x100, divided at
  apply (130 -> 1.30). Shows the float value in the readout.
- Spacing slider: 0 to +40 (1/1000 em). Shows as "0" or "+N".
  Tightens or opens up character spacing without touching size.
- Weight picker: Light (300) / Regular (400) / Medium (500)
  preset cards with Aa previews at each weight.
- Dyslexia-friendly toggle: swaps --font to Verdana (wide x-height,
  distinct letterforms, universally available on Windows). Atkinson
  Hyperlegible as a secondary fallback if system happens to have it.

Glass (new themebar section):
- Four presets: Off / Subtle / Medium / Heavy.
- Each maps to a (blur, tint) pair:
    off:    0px  / 1.00  (solid cards, pre-v1.3 behavior)
    subtle: 8px  / 0.85
    medium: 16px / 0.70
    heavy:  24px / 0.55
- .card now uses rgba(var(--bg-1-rgb), var(--glass-tint)) for the
  background so tint dials opacity without losing theme color.
  backdrop-filter: blur(var(--glass-blur)) saturate(1.15) frosts
  whatever sits behind - gradient mesh, film grain, etc.
- Works best with a gradient enabled (otherwise there's nothing
  interesting behind the cards to blur). Hint line says so.

Layered shadow tokens:
- --shadow-card replaces the old single 12px drop. Composite of
  a tight 1-2px close shadow and a soft 24px far shadow, which
  reads crisp on any background including noisy/gradient canvases.
- Light theme overrides with gentler values.
- Light-mode .card override deleted (the token-based approach
  handles per-theme shadow differences cleanly).

Internals:
- All v1.3 state lives on <html> as CSS custom properties + one
  data-dyslexia attribute. Zero per-element JS styling. Same
  "flip one variable, browser repaints everything" pattern used
  throughout v1.2.
- DEFAULT_SETTINGS adds fontScale, lineHeight, letterSpacing,
  fontWeight, dyslexiaFont, glassMode. Migration-safe via the
  existing loadSettings spread.
- applyThemebar now also calls applyTypography and applyGlass,
  plus their sync functions.
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
