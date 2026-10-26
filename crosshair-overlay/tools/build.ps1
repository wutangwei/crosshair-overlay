# Build a portable exe with PyInstaller (onedir mode).
# Usage:  powershell -ExecutionPolicy Bypass -File tools/build.ps1
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host '== checking PyInstaller ==' -ForegroundColor Cyan
python -m PyInstaller --version 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'PyInstaller not found, installing...' -ForegroundColor Yellow
    python -m pip install --quiet pyinstaller
}

Write-Host '== building ==' -ForegroundColor Cyan
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --noupx `
    --name crosshair `
    --icon assets/crosshair.ico `
    --distpath dist `
    --workpath build `
    --specpath build `
    src/crosshair.py

if ($LASTEXITCODE -eq 0) {
    Write-Host ''
    Write-Host "Done. Output: $root\dist\crosshair\crosshair.exe" -ForegroundColor Green
} else {
    Write-Host 'Build failed.' -ForegroundColor Red
    exit 1
}
