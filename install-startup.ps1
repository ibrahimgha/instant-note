$ErrorActionPreference = "Stop"

$exe = Join-Path $PSScriptRoot "InstantNotes.exe"
$script = Join-Path $PSScriptRoot "instant_notes.pyw"
$icon = Join-Path $PSScriptRoot "note-icon.ico"
$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "Instant Notes.lnk"

if (Test-Path $exe) {
    $target = $exe
    $arguments = ""
} else {
    $python = (Get-Command python -ErrorAction Stop).Source
    $pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"

    if (-not (Test-Path $pythonw)) {
        $pythonw = $python
    }

    $target = $pythonw
    $arguments = "`"$script`""
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $target
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.WindowStyle = 7
$shortcut.Description = "F9/F10 instant local notes"
if (Test-Path $icon) {
    $shortcut.IconLocation = $icon
}
$shortcut.Save()

Write-Host "Created startup shortcut: $shortcutPath"
