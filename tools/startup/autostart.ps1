<#
.SYNOPSIS
  Prizma a Claude po přihlášení do Windows.

.DESCRIPTION
  Založí dvě naplánované úlohy pro přihlášeného uživatele:

    Prizma AutoStart  - uvicorn přes pythonw.exe, tedy bez konzolového okna.
                        Poslouchá jen na 127.0.0.1, takže se appka neobjeví
                        na síti. Po pádu se třikrát pokusí nastartovat znovu.
                        Jakmile server odpovídá, otevře si sám okno appky
                        (serve.py --open) - po přihlášení není co klikat.
    Claude AutoStart  - Claude je Store (MSIX) aplikace, do WindowsApps se
                        zástupcem mířit nedá. Spouští se přes AppUserModelId,
                        což je oficiální cesta: explorer.exe shell:AppsFolder\...

  Proč naplánovaná úloha a ne klasický zástupce ve Startupu: úloha umí zpoždění
  po přihlášení (aby se appka neprala se startem Windows o disk), restart po
  pádu a běh bez viditelného okna. Zástupce neumí nic z toho.

  Prizma se **nespouští přes aplikace.exe**. Ten drží server v job objectu,
  který ho zabije při zavření okna — to je při klikacím spuštění správně,
  ale u služby na pozadí je to přesně to, co nechceme.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\startup\autostart.ps1
  powershell -ExecutionPolicy Bypass -File tools\startup\autostart.ps1 -Remove
#>
param(
  [switch]$Remove,
  [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'

$PrizmaTask = 'Prizma AutoStart'
$ClaudeTask = 'Claude AutoStart'

if ($Remove) {
  foreach ($t in @($PrizmaTask, $ClaudeTask)) {
    if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
      Unregister-ScheduledTask -TaskName $t -Confirm:$false
      Write-Output "odstraněno: $t"
    } else {
      Write-Output "neexistuje:  $t"
    }
  }
  $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Prizma.lnk'
  if (Test-Path $lnk) { Remove-Item $lnk; Write-Output "odstraněno: zástupce Prizma.lnk" }
  return
}

# ---------------------------------------------------------------- Prizma

$repo    = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$backend = Join-Path $repo 'backend'
$pythonw = Join-Path $backend '.venv\Scripts\pythonw.exe'
$dist    = Join-Path $repo 'frontend\dist\index.html'

if (-not (Test-Path $pythonw)) {
  throw "Chybí $pythonw. Nejdřív: python -m venv backend\.venv a pip install -e backend"
}
if (-not (Test-Path $dist)) {
  # Bez buildu server naběhne, ale na / vrátí 404 a vypadá to jako rozbitá appka.
  throw "Chybí $dist. Nejdřív: cd frontend; npm ci; npm run build"
}

# Přes serve.py, ne přímo `-m uvicorn`: pythonw nemá stdout ani stderr, uvicorn
# si při startu staví StreamHandler(sys.stderr) a první zalogovaný řádek proces
# shodí. serve.py obojí nejdřív přesměruje do %LOCALAPPDATA%\Prizma\server.log.
$serve = Join-Path $PSScriptRoot 'serve.py'
$action = New-ScheduledTaskAction -Execute $pythonw `
  -Argument "`"$serve`" --port $Port --open" `
  -WorkingDirectory $backend

# 30 s po přihlášení: plocha, OneDrive a antivirus si mezitím vezmou disk pro sebe.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = 'PT30S'

# -Hidden: úloha se nemá objevit v seznamu běžících úloh na popředí.
# Okno stejně nemá (pythonw), tohle je jen o tom, aby po přihlášení nebylo
# vidět vůbec nic než hotová appka.
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -Hidden `
  -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
  -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $PrizmaTask -Action $action -Trigger $trigger `
  -Settings $settings -Description 'Prizma - lokální server na http://127.0.0.1:8000' `
  -Force | Out-Null
Write-Output "založeno: $PrizmaTask (127.0.0.1:$Port, okno se otevře samo)"

# ---------------------------------------------------------------- Claude

$claude = Get-StartApps | Where-Object { $_.Name -eq 'Claude' } | Select-Object -First 1
if (-not $claude) {
  Write-Warning "Claude nenalezen v seznamu aplikací - úloha se nezaloží."
} else {
  $cAction  = New-ScheduledTaskAction -Execute 'explorer.exe' -Argument "shell:AppsFolder\$($claude.AppID)"
  $cTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
  $cTrigger.Delay = 'PT45S'   # až po Prizmě, ať se nervou o disk
  $cSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -Hidden -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew

  Register-ScheduledTask -TaskName $ClaudeTask -Action $cAction -Trigger $cTrigger `
    -Settings $cSettings -Description "Claude ($($claude.AppID))" -Force | Out-Null
  Write-Output "založeno: $ClaudeTask ($($claude.AppID))"
}

# ---------------------------------------------------------------- zástupce

# Server běží na pozadí, ale okno appky si musí někdo otevřít. --app= dá čisté
# okno bez adresního řádku a panelů, takže to vypadá jako aplikace, ne jako tab.
# Složené závorky jsou tu nutné: bez nich se `$env:ProgramFiles(x86)` rozpadne
# na proměnnou `$env:ProgramFiles` a literál `(x86)`, cesta neexistuje a skript
# tiše skončí bez zástupce.
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) { $edge = "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe" }
if (Test-Path $edge) {
  $shell = New-Object -ComObject WScript.Shell
  $lnk = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Prizma.lnk'))
  $lnk.TargetPath = $edge
  $lnk.Arguments  = "--app=http://127.0.0.1:$Port"
  $icon = Join-Path $PSScriptRoot 'prizma.ico'
  if (Test-Path $icon) { $lnk.IconLocation = $icon }
  $lnk.Description = 'Prizma'
  $lnk.Save()
  Write-Output "založeno: zástupce Prizma.lnk na ploše"
} else {
  Write-Warning "Edge nenalezen - zástupce se nezaložil. Appka je na http://127.0.0.1:$Port"
}
