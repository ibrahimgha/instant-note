$ErrorActionPreference = "Stop"

$pyinstaller = (python -m PyInstaller --version) 2>$null
if (-not $pyinstaller) {
    python -m pip install pyinstaller
}

$entry = Join-Path $PSScriptRoot "instant_notes.pyw"
$icon = Join-Path $PSScriptRoot "note-icon.ico"
$work = Join-Path $PSScriptRoot "build"

python -m PyInstaller `
    --onefile `
    --windowed `
    --name InstantNotes `
    --icon "$icon" `
    --distpath "$PSScriptRoot" `
    --workpath "$work" `
    --specpath "$work" `
    --noconfirm `
    "$entry"

Write-Host "Built: $(Join-Path $PSScriptRoot 'InstantNotes.exe')"
