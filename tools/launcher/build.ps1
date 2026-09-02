# Rebuilds aplikace.exe from Launcher.cs.
#
# csc.exe is part of Windows — the .NET Framework 4 compiler lives under
# C:\Windows\Microsoft.NET and needs nothing installed. That is the whole
# reason the launcher is C#: PyInstaller cannot run on this machine (pip hits
# a RecursionError in setuptools), and a build step that only works once is
# not a build step.
#
#   powershell -ExecutionPolicy Bypass -File tools\launcher\build.ps1

# Uloženo s UTF-8 BOM: Windows PowerShell 5.1 by jinak četlo tenhle soubor
# jako ANSI a každá česká diakritika by skončila jako chyba parseru.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$source = Join-Path $PSScriptRoot "Launcher.cs"
$output = Join-Path $root "aplikace.exe"

$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) {
    $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path $csc)) {
    throw "csc.exe nenalezen — chybí .NET Framework 4."
}

# /platform:anycpu so the same file runs on a 32-bit machine too; the launcher
# only starts processes and polls a socket, so it has no reason to care.
& $csc /nologo /target:exe /platform:anycpu /optimize+ ("/out:" + $output) $source
if ($LASTEXITCODE -ne 0) {
    throw "Kompilace selhala."
}

$size = [math]::Round((Get-Item $output).Length / 1KB, 1)
Write-Host ("Hotovo: {0} ({1} kB)" -f $output, $size)
