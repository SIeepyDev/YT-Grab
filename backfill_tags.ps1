# backfill_tags.ps1 - one-time retroactive tagging.
# Walks your git log, finds every commit whose subject starts with
# "vX.X:" (the convention all your old commit_vX.X.ps1 scripts used),
# and tags it. Then pushes every new tag to GitHub in one shot.
#
# Result: the Releases page on GitHub lights up with the full history
# (v1.0 through the latest) because the release.yml action fires once
# per pushed tag and builds each Release from its README section.
#
# Safe to re-run - it skips any version that's already tagged.

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path','User')

Write-Host ""
Write-Host "== Backfill version tags ==" -ForegroundColor Cyan
Write-Host ""

$log = git log --pretty=format:"%H %s"

$tagged = 0
$skipped = 0
foreach ($line in $log) {
    # Match commits whose subject starts with "vX.X:" or "vX.X.X:"
    if ($line -match '^([a-f0-9]+)\s+(v\d+\.\d+(?:\.\d+)?):') {
        $hash    = $Matches[1]
        $version = $Matches[2]

        $existing = git tag -l $version
        if ($existing) {
            Write-Host "  $version already tagged - skip" -ForegroundColor DarkGray
            $skipped++
            continue
        }

        $subject = $line.Substring($hash.Length + 1)
        git tag -a $version -m $subject $hash
        Write-Host "  tagged $version -> $($hash.Substring(0,7))" -ForegroundColor Green
        $tagged++
    }
}

Write-Host ""
if ($tagged -eq 0) {
    Write-Host "No new tags to push. ($skipped already present.)" -ForegroundColor Yellow
} else {
    Write-Host "Pushing $tagged new tag(s) to origin..." -ForegroundColor Cyan
    git push origin --tags
    Write-Host ""
    Write-Host "Done. GitHub Action will auto-create a Release for each tag." -ForegroundColor Green
    Write-Host "  https://github.com/SIeepyDev/yt-grab/releases"
}

Write-Host ""
pause
