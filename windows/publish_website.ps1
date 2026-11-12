<#
 Stage the Windows portable build for the official website (NOT run automatically, does not commit / deploy).
 PowerShell 5.1 compatible.

 The in-app updater (windows\win_support.py) reads, in this order:
   1. https://powerup.dancingarden.com/downloads/toolbox/win-version.json   (+ PowerUpToolbox-portable.zip beside it)
   2. GitHub Releases tag win-v<X.Y.Z>  (fallback, only while the repository is public)

 Steps:
   1. Edit windows\VERSION and commit it.
   2. powershell -ExecutionPolicy Bypass -File windows\build_windows.ps1
   3. powershell -ExecutionPolicy Bypass -File windows\publish_website.ps1 -Notes "change 1`nchange 2"
      -> copies the zip and writes win-version.json (version, date, sha256, size, changelog) into
         <website repo>\public\downloads\toolbox\
   4. In the website repo: review, commit, push, then run its deploy workflow (manual).
 Each notes line becomes one line of the in-app changelog.
#>
param(
  [string]$Notes = "",
  [string]$WebsiteRepo = "",
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $WebsiteRepo) { $WebsiteRepo = Join-Path (Split-Path -Parent $Root) "powerup" }
$ver = (Get-Content (Join-Path $Root "windows\VERSION") -Raw).Trim()
if ($ver -notmatch '^\d+(\.\d+){0,3}$') { throw "Bad version in windows\VERSION: $ver" }
$asset = "PowerUpToolbox-portable.zip"
$zip = Join-Path $Root "release_win\$asset"
if (-not (Test-Path -LiteralPath $zip)) { throw "Missing $zip - run windows\build_windows.ps1 first" }
$builtVer = Join-Path $Root "release_win\PowerUpToolbox\_internal\VERSION"
if ((Test-Path -LiteralPath $builtVer) -and ((Get-Content -LiteralPath $builtVer -Raw).Trim() -ne $ver)) {
  throw "release_win was built from another version than windows\VERSION ($ver) - rebuild first"
}
$dest = Join-Path $WebsiteRepo "public\downloads\toolbox"
if (-not (Test-Path -LiteralPath $dest)) { throw "Website downloads folder not found: $dest (pass -WebsiteRepo)" }

$sha = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLower()
$size = (Get-Item -LiteralPath $zip).Length
if (-not $Notes) { $Notes = "PowerUp Toolbox for Windows v$ver" }
$lines = @($Notes -split "`r?`n" | ForEach-Object { $_.Trim().TrimStart("-", "*", " ") } | Where-Object { $_ })

$manifest = [ordered]@{
  version      = $ver
  name         = "PowerUp Toolbox for Windows"
  release_date = (Get-Date -Format "yyyy-MM-dd")
  asset        = $asset
  sha256       = $sha
  size         = $size
  changelog    = $lines
}
$json = $manifest | ConvertTo-Json -Depth 4

Write-Host "Version : $ver"
Write-Host "Zip     : $zip ($size bytes)"
Write-Host "SHA256  : $sha"
Write-Host "Dest    : $dest"
if ($DryRun) { Write-Host "[DryRun] manifest would be:"; Write-Host $json; exit 0 }

Copy-Item -LiteralPath $zip -Destination (Join-Path $dest $asset) -Force
# UTF-8 without BOM (PowerShell 5.1's -Encoding utf8 would add one)
[System.IO.File]::WriteAllText((Join-Path $dest "win-version.json"), $json + "`n", (New-Object System.Text.UTF8Encoding($false)))
$check = (Get-FileHash -LiteralPath (Join-Path $dest $asset) -Algorithm SHA256).Hash.ToLower()
if ($check -ne $sha) { throw "copied zip does not match its checksum" }
Write-Host "Staged. Next: commit + push the website repo, then run its deploy workflow."
