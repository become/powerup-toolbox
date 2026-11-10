<#
 Publish the Windows portable build as a GitHub Release (NOT run automatically).
 PowerShell 5.1 compatible. Requires: gh CLI logged in (gh auth login) with push rights to become/powerup-toolbox.

 Convention (matches what the in-app updater looks for, see windows\win_support.py):
   tag   = win-v<X.Y.Z>        (windows\VERSION holds X.Y.Z; Mac releases use other tags)
   asset = PowerUpToolbox-portable.zip

 Steps:
   1. Edit windows\VERSION (e.g. 1.0.1) and commit it.
   2. powershell -ExecutionPolicy Bypass -File windows\build_windows.ps1
   3. powershell -ExecutionPolicy Bypass -File windows\publish_release.ps1 -Notes "- change 1`n- change 2"
      (add -DryRun to only print what would happen)
 Each notes line becomes one line of the in-app changelog.
#>
param(
  [string]$Notes = "",
  [string]$Repo = "become/powerup-toolbox",
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$ver = (Get-Content (Join-Path $Root "windows\VERSION") -Raw).Trim()
$tag = "win-v$ver"
$zip = Join-Path $Root "release_win\PowerUpToolbox-portable.zip"
if (-not (Test-Path $zip)) { throw "Missing $zip - run windows\build_windows.ps1 first" }
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "gh CLI not found" }
if (-not $Notes) { $Notes = "PowerUp Toolbox for Windows v$ver" }

Write-Host "Repo : $Repo"
Write-Host "Tag  : $tag"
Write-Host "Asset: $zip"
if ($DryRun) { Write-Host "[DryRun] would run: gh release create $tag <zip> --repo $Repo --title ..."; exit 0 }

$confirm = Read-Host "Create PUBLIC release $tag on $Repo? (type yes)"
if ($confirm -ne "yes") { Write-Host "Aborted."; exit 1 }
gh release create $tag $zip --repo $Repo --title "PowerUp Toolbox for Windows v$ver" --notes $Notes
if ($LASTEXITCODE -ne 0) { throw "gh release create failed" }
Write-Host "Done."
