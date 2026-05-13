$ErrorActionPreference = "Stop"

$script = Join-Path $PSScriptRoot "instant_notes.pyw"
$exe = Join-Path $PSScriptRoot "InstantNotes.exe"
$processes = Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -and
        (
            (
                $_.CommandLine -match [regex]::Escape($script) -and
                $_.Name -match '^pythonw?(\d+(\.\d+)*)?\.exe$'
            ) -or
            (
                $_.CommandLine -match [regex]::Escape($exe) -and
                $_.Name -ieq 'InstantNotes.exe'
            )
        )
    }

if (-not $processes) {
    Write-Host "Instant Notes is not running."
    exit 0
}

foreach ($process in $processes) {
    Stop-Process -Id $process.ProcessId -Force
    Write-Host "Stopped Instant Notes process $($process.ProcessId)."
}
