<#
YT Grab -- public release publisher.

Reads the current version from .release-please-manifest.json and cuts
a matching vX.Y.Z release on the PUBLIC distribution repo
    github.com/SIeepyDev/YTGrab

then uploads the three build artifacts as release assets:
    dist\YTGrab.exe
    dist\YTGrabUninstaller.exe
    dist\YTGrabSetup.exe

The private development repo (SIeepyDev/yt-grab) is untouched -- it
stays source-only. release-please still cuts versioned release PRs
there as normal. This script only publishes binaries to the public
channel your friends install from.

Auth: set $env:GH_PAT to a personal access token with `repo` scope on
the public repo (classic PAT or fine-grained with Contents:RW).

Usage:
    release.bat            (from repo root, after build.bat succeeds)
or:
    powershell -NoProfile -ExecutionPolicy Bypass -File release.ps1
#>

[CmdletBinding()]
param(
    [string]$Owner = "SIeepyDev",
    [string]$Repo  = "YT-Grab",
    [switch]$Draft,
    [switch]$Prerelease,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

function Fail($msg) {
    Write-Host ""
    Write-Host "[release] ERROR: $msg" -ForegroundColor Red
    exit 1
}

function Info($msg)    { Write-Host "[release] $msg" -ForegroundColor Cyan }
function Success($msg) { Write-Host "[release] $msg" -ForegroundColor Green }

# --- Preflight --------------------------------------------------------

$token = $env:GH_PAT
if ([string]::IsNullOrWhiteSpace($token)) {
    Fail "GH_PAT env var is not set. Set a PAT with repo scope on $Owner/$Repo."
}

$repoRoot = Split-Path -Parent $PSCommandPath
Set-Location $repoRoot

$manifest = Join-Path $repoRoot ".release-please-manifest.json"
if (-not (Test-Path $manifest)) {
    Fail ".release-please-manifest.json not found at $manifest"
}

$manifestData = Get-Content $manifest -Raw | ConvertFrom-Json
# Manifest shape: { ".": "1.6.1" }  -- pull the "." entry.
$version = $manifestData.'.'
if ([string]::IsNullOrWhiteSpace($version)) {
    Fail "Couldn't read version from .release-please-manifest.json"
}
$tag = "v$version"
Info "Version: $version (tag $tag)"

# Check build artifacts exist.
$distDir = Join-Path $repoRoot "dist"
$assets = @(
    (Join-Path $distDir "YTGrab.exe"),
    (Join-Path $distDir "YTGrabUninstaller.exe"),
    (Join-Path $distDir "YTGrabSetup.exe")
)
foreach ($a in $assets) {
    if (-not (Test-Path $a)) {
        Fail "Missing build artifact: $a  (run build.bat first)"
    }
}

# --- API helpers ------------------------------------------------------

$apiBase = "https://api.github.com/repos/$Owner/$Repo"
$uploadBase = "https://uploads.github.com/repos/$Owner/$Repo"
$commonHeaders = @{
    "Authorization" = "Bearer $token"
    "Accept"        = "application/vnd.github+json"
    "User-Agent"    = "YTGrab-Release-Script"
    "X-GitHub-Api-Version" = "2022-11-28"
}

function Invoke-Gh($method, $path, $body) {
    $url = "$apiBase$path"
    $params = @{
        Method  = $method
        Uri     = $url
        Headers = $commonHeaders
    }
    if ($null -ne $body) {
        $params.Body = ($body | ConvertTo-Json -Depth 10 -Compress)
        $params.ContentType = "application/json"
    }
    return Invoke-RestMethod @params
}

# --- Find or create the release --------------------------------------
#
# Default path: if release-please already published vX.Y.Z (with its
# auto-generated changelog body), we attach assets to it instead of
# deleting + recreating.  Pass -Force to nuke and rebuild the release
# from this script's boilerplate body.

$existing = $null
try {
    $existing = Invoke-Gh "GET" "/releases/tags/$tag" $null
} catch {
    # 404 = no existing release; anything else is real.
    if ($_.Exception.Response.StatusCode.value__ -ne 404) { throw }
}

$release = $null
if ($existing) {
    if ($Force) {
        Info "Deleting existing release $tag (id=$($existing.id)) because -Force was passed..."
        Invoke-Gh "DELETE" "/releases/$($existing.id)" $null | Out-Null
        # Also delete the underlying tag ref so create-release can recreate it.
        try {
            Invoke-Gh "DELETE" "/git/refs/tags/$tag" $null | Out-Null
        } catch {
            # Tag may not exist separately; not fatal.
        }
    } else {
        Info "Release $tag already exists (id=$($existing.id)); attaching assets to it."
        Info "  (pass -Force to delete + recreate instead)"
        $release = $existing
    }
}

if (-not $release) {
    Info "Creating release $tag on $Owner/$Repo..."
    $releaseBody = @"
YT Grab $tag

Download **YTGrabSetup.exe** below to install or update.

See the [README](https://github.com/$Owner/$Repo) for install steps and notes on Smart App Control.
"@

    $createPayload = @{
        tag_name         = $tag
        target_commitish = "main"
        name             = $tag
        body             = $releaseBody
        draft            = [bool]$Draft
        prerelease       = [bool]$Prerelease
    }
    $release = Invoke-Gh "POST" "/releases" $createPayload
    Success "Release created: id=$($release.id)"
}

# --- Clean up conflicting assets -------------------------------------
#
# If a prior run (or a half-finished upload) left any asset with the
# same filename on this release, delete it first so the upload below
# doesn't 422 on "already_exists".

$assetNames = $assets | ForEach-Object { Split-Path $_ -Leaf }
if ($release.assets) {
    foreach ($existingAsset in $release.assets) {
        if ($assetNames -contains $existingAsset.name) {
            Info "Removing existing asset $($existingAsset.name) (id=$($existingAsset.id))..."
            Invoke-Gh "DELETE" "/releases/assets/$($existingAsset.id)" $null | Out-Null
        }
    }
}

# --- Upload assets ---------------------------------------------------

foreach ($assetPath in $assets) {
    $assetName = Split-Path $assetPath -Leaf
    Info "Uploading $assetName..."

    $uploadUrl = "$uploadBase/releases/$($release.id)/assets?name=$([Uri]::EscapeDataString($assetName))"
    $uploadHeaders = $commonHeaders.Clone()
    $uploadHeaders["Content-Type"] = "application/octet-stream"

    try {
        Invoke-RestMethod -Method Post -Uri $uploadUrl `
            -Headers $uploadHeaders `
            -InFile $assetPath | Out-Null
        Success "  uploaded $assetName"
    } catch {
        Fail "Upload failed for $assetName : $($_.Exception.Message)"
    }
}

# --- Done ------------------------------------------------------------

Write-Host ""
Success "Published $tag to https://github.com/$Owner/$Repo/releases/tag/$tag"
Write-Host ""
Info "Friends install URL:  https://github.com/$Owner/$Repo/releases/latest"
Write-Host ""
