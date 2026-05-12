$ErrorActionPreference = "Stop"

$python = (Get-Command python -ErrorAction Stop).Source
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"

if (-not (Test-Path $pythonw)) {
    $pythonw = $python
}

$script = Join-Path $PSScriptRoot "instant_notes.pyw"
Start-Process -FilePath $pythonw -ArgumentList "`"$script`"" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
