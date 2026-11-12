<#
 Build the PowerUp Toolbox Windows portable package (PyInstaller onedir).
 PowerShell 5.1 compatible.  Usage:  powershell -ExecutionPolicy Bypass -File windows\build_windows.ps1
 Output: build_win\ (work files), release_win\PowerUpToolbox\ (onedir), release_win\PowerUpToolbox-portable.zip
 Optional: -Python <path to python.exe>
#>
param([string]$Python = "")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if ($Python) {
  $resolved = Get-Command $Python -ErrorAction SilentlyContinue
  if ($resolved) { $Python = $resolved.Source }
}
if (-not $Python -or -not (Test-Path $Python)) {
  $pyCmd = Get-Command python -ErrorAction SilentlyContinue
  if ($pyCmd) { $Python = $pyCmd.Source }
  else { $Python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe" }
}
if (-not (Test-Path $Python)) { throw "Python not found: $Python" }

& $Python -m pip install --quiet pyinstaller pywebview pillow openpyxl python-docx python-pptx pypdfium2 msoffcrypto-tool
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# PyInstaller does not syntax-check bundled modules; fail early instead of shipping a broken exe.
& $Python -m py_compile (Join-Path $Root "server.py") (Join-Path $Root "windows\launcher.py") (Join-Path $Root "windows\win_support.py") (Join-Path $Root "gphotos.py") (Join-Path $Root "local_albums.py")
if ($LASTEXITCODE -ne 0) { throw "syntax check failed" }

$Work = Join-Path $Root "build_win"
$Rel  = Join-Path $Root "release_win"
# Never overwrite a release folder whose exe is currently running (it would crash the user's session).
$running = @(Get-Process -Name PowerUpToolbox -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Rel, [System.StringComparison]::OrdinalIgnoreCase) })
if ($running.Count -gt 0) {
  $Rel = Join-Path $Root "release_win_next"
  Write-Host "WARNING: PowerUpToolbox.exe is running from release_win. Building into release_win_next instead; close the app and swap folders manually." -ForegroundColor Yellow
}
New-Item -ItemType Directory -Force -Path $Work | Out-Null
New-Item -ItemType Directory -Force -Path $Rel  | Out-Null

# multi-size .ico from assets\app_icon.png (fallback to assets\app.ico)
$mkico = @'
import sys
from PIL import Image
src, dst = sys.argv[1], sys.argv[2]
im = Image.open(src).convert("RGBA")
im.save(dst, sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
'@
$mkPy = Join-Path $Work "make_ico.py"
Set-Content -Path $mkPy -Value $mkico -Encoding UTF8
$png = Join-Path $Root "assets\app_icon.png"
$ico = Join-Path $Work "app_multi.ico"
if (Test-Path $png) { & $Python $mkPy $png $ico } else { Copy-Item (Join-Path $Root "assets\app.ico") $ico }
if ($LASTEXITCODE -ne 0) { throw "icon conversion failed" }

$env:POWERUP_REPO_ROOT = $Root
$Out = Join-Path $Rel "PowerUpToolbox"
if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
& $Python -m PyInstaller --noconfirm --clean --workpath (Join-Path $Work "pyi") --distpath $Rel (Join-Path $Root "windows\PowerUpToolbox.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$Zip = Join-Path $Rel "PowerUpToolbox-portable.zip"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
Compress-Archive -Path $Out -DestinationPath $Zip -CompressionLevel Optimal
$ver = (Get-Content (Join-Path $Root "windows\VERSION") -Raw).Trim()
Write-Host ("Built v{0}: {1}" -f $ver, $Out)
Write-Host ("Zip: {0} ({1:N1} MB)" -f $Zip, ((Get-Item $Zip).Length / 1MB))
