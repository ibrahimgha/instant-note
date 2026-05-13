$ErrorActionPreference = "Stop"

$exe = Join-Path $PSScriptRoot "InstantNotes.exe"
if (Test-Path $exe) {
    Start-Process -FilePath $exe -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
    exit 0
}

$python = (Get-Command python -ErrorAction Stop).Source
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"

if (-not (Test-Path $pythonw)) {
    $pythonw = $python
}

$script = Join-Path $PSScriptRoot "instant_notes.pyw"
Start-Process -FilePath $pythonw -ArgumentList "`"$script`"" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
