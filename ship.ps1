# ship.ps1 - one-shot ship to GitHub.
# Right-click -> Run with PowerShell whenever you bump the version.
#
# What it does:
#   1. Reads the current version from index.html (looks for "YT Grab vX.X").
#   2. Pulls the matching "### vX.X" section out of README.md.
#   3. Commits everything staged with that block as the message.
#   4. Tags the commit with vX.X and pushes both the commit and the tag.
#   5. The release.yml GitHub Action then auto-creates a polished
#      GitHub Release with the README section as the body.
#
# So your only job each release: bump version in index.html, write the
# "### vX.X" entry in README.md, double-click this script. Done.

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path','User')

# --- Detect version from index.html.
# The version lives on the "about-line" div in the sidebar, e.g.
#   <div class="about-line"><strong ...>YT Grab</strong> v1.3</div>
# We match the about-line class and extract the first vX.X on that line.
# Fallback also matches "YT Grab ... vX.X" with any tags between (stable
# if the about-line markup changes).
$versionMatch = Select-String -Path 'index.html' -Pattern 'class="about-line"[^<]*(?:<[^>]+>[^<]*)*v(\d+\.\d+(?:\.\d+)?)' |
                Select-Object -First 1
if (-not $versionMatch) {
    $versionMatch = Select-String -Path 'index.html' -Pattern 'YT Grab(?:<[^>]+>)*\s*v(\d+\.\d+(?:\.\d+)?)' |
                    Select-Object -First 1
}
if (-not $versionMatch) {
    Write-Host "Could not detect version in index.html." -ForegroundColor Red
    Write-Host "Make sure the about-line div reads like: 'YT Grab' v1.3" -ForegroundColor Yellow
    pause; exit 1
}
$version = $versionMatch.Matches[0].Groups[1].Value
$tag     = "v$version"

Write-Host ""
Write-Host "== YT Grab ship $tag ==" -ForegroundColor Cyan
Write-Host ""

# --- Bail if this tag already exists locally
$existing = git tag -l $tag
if ($existing) {
    Write-Host "Tag $tag already exists. Bump the version in index.html before shipping again." -ForegroundColor Yellow
    pause; exit 1
}

# --- Pull the matching section out of README.md
$readme = Get-Content README.md -Raw
$pattern = "(?ms)^### $([regex]::Escape($tag))( |$|\xE2\x80\x94).*?(?=^### v|\Z)"
$match = [regex]::Match($readme, $pattern)
if (-not $match.Success) {
    # Fallback - try plain "### vX.X" without trailing space match
    $pattern2 = "(?ms)^### $([regex]::Escape($tag))\b.*?(?=^### v|\Z)"
    $match    = [regex]::Match($readme, $pattern2)
}
if (-not $match.Success) {
    Write-Host "No '### $tag' section in README.md. Add the changelog block first." -ForegroundColor Red
    pause; exit 1
}
$section = $match.Value.Trim()

# Headline = the heading line minus "### "
$headline = ($section -split "`n")[0] -replace '^### ', ''

# Body = section minus heading line
$body = ($section -split "`n", 2)[1].Trim()

# --- Stage + show what's about to ship
git add .
git status --short

# --- Commit (headline as subject, body as message body)
$commitMsg = "$headline`n`n$body"
git commit -m $commitMsg | Out-Host

# --- Tag the commit with an annotated tag
git tag -a $tag -m $headline

# --- Push commit + tag
Write-Host ""
Write-Host "Pushing commit + tag..." -ForegroundColor Cyan
git push
git push origin $tag

Write-Host ""
Write-Host "Shipped $tag" -ForegroundColor Green
Write-Host "GitHub Action will create a Release in ~30s:" -ForegroundColor Green
Write-Host "  https://github.com/SIeepyDev/yt-grab/releases/tag/$tag"
Write-Host ""
pause
