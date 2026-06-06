# Build the canvas-dl CLI and GUI standalone binaries on Windows with PyInstaller.
# Usage:  ./scripts/build_binaries.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host ">> Installing build dependencies"
python -m pip install -e ".[build]"

Write-Host ">> Building CLI (one-file)"
python -m PyInstaller --noconfirm --clean canvas-dl-cli.spec

Write-Host ">> Building GUI (one-dir)"
python -m PyInstaller --noconfirm --clean canvas-dl-gui.spec

Write-Host ">> Done. Artifacts in .\dist:"
Get-ChildItem dist
