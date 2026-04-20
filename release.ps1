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
    [string]$Repo  = "YTGrab",
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

# --- Handle existing release -----------------------------------------

$existing = $null
try {
    $existing = Invoke-Gh "GET" "/releases/tags/$tag" $null
} catch {
    # 404 = no existing release; anything else is real.
    if ($_.Exception.Response.StatusCode.value__ -ne 404) { throw }
}

if ($existing) {
    if (-not $Force) {
        Fail "Release $tag already exists on $Owner/$Repo. Re-run with -Force to delete + recreate, or bump the version first."
    }
    Info "Deleting existing release $tag (id=$($existing.id)) because -Force was passed..."
    Invoke-Gh "DELETE" "/releases/$($existing.id)" $null | Out-Null
    # Also delete the underlying tag ref so create-release can recreate it.
    try {
        Invoke-Gh "DELETE" "/git/refs/tags/$tag" $null | Out-Null
    } catch {
        # Tag may not exist separately; not fatal.
    }
}

# --- Create the release ----------------------------------------------

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
