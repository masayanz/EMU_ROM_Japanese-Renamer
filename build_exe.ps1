# ROM Japanese Renamer v1.8.0
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

Write-Host 'Installing/updating dependencies...'
py -m pip install -U google-genai pyinstaller

Write-Host 'Building one-file GUI EXE...'
py -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name 'ROM-Japanese-Renamer' `
  --collect-submodules google.genai `
  .\rom_renamer_gui.py

Write-Host ''
Write-Host 'Build complete:'
Write-Host (Join-Path $PSScriptRoot 'dist\ROM-Japanese-Renamer.exe')
