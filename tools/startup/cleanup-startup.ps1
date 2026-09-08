<#
.SYNOPSIS
  Vypne všechno, co se spouští po přihlášení, kromě povoleného seznamu.

.DESCRIPTION
  Sahá jen tam, kam sahá i Správce úloh -> Aplikace po spuštění:

    * hodnoty v klíčích Run (HKCU, HKLM, HKLM\WOW6432Node)
    * zástupci ve složce Po spuštění
    * naplánované úlohy mimo \Microsoft\ spouštěné při přihlášení nebo startu

  Vypnutí se dělá stejným způsobem jako ve Správci úloh: první bajt hodnoty
  v StartupApproved se přepne na 3. Samotný záznam v Run zůstane, takže se dá
  vrátit zpátky a nic se neodinstaluje.

  **Služby se nedotýká.** Antivirus, ovladače a systémové věci běží jako služby,
  ne jako startup položky, a vypínat je kvůli rychlejšímu bootu je přesně ta
  kategorie zásahů, po které přestane fungovat něco úplně jiného.

  Před každou změnou se uloží záloha do
  %LOCALAPPDATA%\Prizma\startup-backup.json. -Undo z ní přesně obnoví stav.

.PARAMETER Apply
  Provede změny. Bez něj skript jen vypíše, co by udělal.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\startup\cleanup-startup.ps1
  powershell -ExecutionPolicy Bypass -File tools\startup\cleanup-startup.ps1 -Apply
  powershell -ExecutionPolicy Bypass -File tools\startup\cleanup-startup.ps1 -Undo
#>
param(
  [switch]$Apply,
  [switch]$Undo
)

$ErrorActionPreference = 'Stop'

# Co zůstane zapnuté, ať se vypíná cokoli.
#
#   Norton*, SecurityHealth - bezpečnostní software. Vypnout ochranu kvůli
#     startu je špatný obchod za každých okolností.
#   OneDrive - na téhle mašině je v OneDrivu celá plocha včetně projektů.
#     Vypnutý OneDrive neznamená pomalejší synchronizaci, ale žádnou.
#   Prizma/Claude - to je celý smysl téhle úpravy.
$Keep = @(
  'Norton*', 'SecurityHealth*', 'AVGUI*', 'Windows Security*', 'SecurityHealthSystray*',
  'OneDrive*',
  'Prizma*', 'Claude*'
)

function Test-Keep([string]$name) {
  foreach ($p in $Keep) { if ($name -like $p) { return $true } }
  return $false
}

$RunKeys = @(
  @{ Run = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
     Approved = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run' },
  @{ Run = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
     Approved = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run' },
  @{ Run = 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run'
     Approved = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32' }
)
$FolderApproved = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder'
$StartupFolders = @(
  "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup",
  "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\Startup"
)

$BackupDir  = Join-Path $env:LOCALAPPDATA 'Prizma'
$BackupFile = Join-Path $BackupDir 'startup-backup.json'

# Bajty, kterými Správce úloh značí stav. Zbytek hodnoty je časové razítko
# posledního přepnutí; při obnově se zapisuje celá původní hodnota, ne jen bajt.
$ENABLED  = [byte[]](2,0,0,0,0,0,0,0,0,0,0,0)
$DISABLED = [byte[]](3,0,0,0,0,0,0,0,0,0,0,0)

function Get-ApprovalState($approvedPath, $name) {
  if (-not (Test-Path $approvedPath)) { return 'enabled' }
  $v = (Get-ItemProperty -Path $approvedPath -Name $name -ErrorAction SilentlyContinue).$name
  if ($null -eq $v) { return 'enabled' }   # bez záznamu = zapnuto
  if ($v[0] -band 1) { return 'disabled' } else { return 'enabled' }
}

# ------------------------------------------------------------------ Undo

if ($Undo) {
  if (-not (Test-Path $BackupFile)) { throw "Záloha $BackupFile neexistuje - není z čeho obnovovat." }
  $backup = Get-Content $BackupFile -Raw -Encoding utf8 | ConvertFrom-Json

  foreach ($item in $backup.registry) {
    if (-not (Test-Path $item.approved)) { New-Item -Path $item.approved -Force | Out-Null }
    if ($null -eq $item.original) {
      # Původně tam žádný záznam nebyl - smazat, ne zapsat "zapnuto".
      Remove-ItemProperty -Path $item.approved -Name $item.name -ErrorAction SilentlyContinue
      Write-Output "obnoveno (smazán záznam): $($item.name)"
    } else {
      Set-ItemProperty -Path $item.approved -Name $item.name -Value ([byte[]]$item.original) -Type Binary
      Write-Output "obnoveno: $($item.name)"
    }
  }
  foreach ($t in $backup.tasks) {
    if (Get-ScheduledTask -TaskName $t.name -TaskPath $t.path -ErrorAction SilentlyContinue) {
      Enable-ScheduledTask -TaskName $t.name -TaskPath $t.path | Out-Null
      Write-Output "obnovena úloha: $($t.path)$($t.name)"
    }
  }
  Write-Output "`nHotovo. Změny se projeví po dalším přihlášení."
  return
}

# ------------------------------------------------------------------ sběr

$plan = @()

foreach ($pair in $RunKeys) {
  if (-not (Test-Path $pair.Run)) { continue }
  $key = Get-Item $pair.Run
  foreach ($name in $key.GetValueNames()) {
    if (Test-Keep $name) { continue }
    if ((Get-ApprovalState $pair.Approved $name) -eq 'disabled') { continue }
    $orig = $null
    if (Test-Path $pair.Approved) {
      $orig = (Get-ItemProperty -Path $pair.Approved -Name $name -ErrorAction SilentlyContinue).$name
    }
    $plan += [pscustomobject]@{
      kind = 'registry'; name = $name; approved = $pair.Approved
      original = $orig; detail = [string]$key.GetValue($name)
    }
  }
}

foreach ($dir in $StartupFolders) {
  if (-not (Test-Path $dir)) { continue }
  foreach ($f in Get-ChildItem $dir -File) {
    if (Test-Keep $f.Name) { continue }
    if ((Get-ApprovalState $FolderApproved $f.Name) -eq 'disabled') { continue }
    $orig = (Get-ItemProperty -Path $FolderApproved -Name $f.Name -ErrorAction SilentlyContinue).$($f.Name)
    $plan += [pscustomobject]@{
      kind = 'registry'; name = $f.Name; approved = $FolderApproved
      original = $orig; detail = $f.FullName
    }
  }
}

$tasks = Get-ScheduledTask | Where-Object {
  $_.TaskPath -notlike '\Microsoft\*' -and $_.State -ne 'Disabled' -and
  ($_.Triggers | Where-Object { $_.CimClass.CimClassName -match 'Logon|Boot' })
} | Where-Object { -not (Test-Keep $_.TaskName) }

# ------------------------------------------------------------------ výpis

Write-Output "K vypnutí ($($plan.Count) položek Run/Startup, $(@($tasks).Count) úloh):`n"
foreach ($p in $plan) { Write-Output ("  [Run]   {0,-38} {1}" -f $p.name, $p.detail) }
foreach ($t in $tasks) { Write-Output ("  [Úloha] {0,-38} {1}" -f $t.TaskName, $t.TaskPath) }
Write-Output "`nZůstává zapnuté: $($Keep -join ', ')"

if (-not $Apply) {
  Write-Output "`nZatím se nic nezměnilo. Spusť znovu s -Apply."
  return
}

# ------------------------------------------------------------------ Apply

New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
@{
  savedAt  = (Get-Date).ToString('o')
  registry = @($plan | ForEach-Object { @{ name = $_.name; approved = $_.approved; original = $_.original } })
  tasks    = @($tasks | ForEach-Object { @{ name = $_.TaskName; path = $_.TaskPath } })
} | ConvertTo-Json -Depth 6 | Out-File $BackupFile -Encoding utf8
Write-Output "`nzáloha: $BackupFile"

# Zápis do HKLM chce práva správce. Ať to nespadne na první takové položce
# a zbytek se stihne — položky pod HKCU (kterých je většina) i naplánované
# úlohy jdou vypnout bez elevace. Co nešlo, se vypíše na konci pohromadě.
$needsAdmin = @()

foreach ($p in $plan) {
  try {
    if (-not (Test-Path $p.approved)) { New-Item -Path $p.approved -Force -ErrorAction Stop | Out-Null }
    Set-ItemProperty -Path $p.approved -Name $p.name -Value $DISABLED -Type Binary -ErrorAction Stop
    Write-Output "vypnuto: $($p.name)"
  } catch [System.Security.SecurityException] {
    $needsAdmin += $p.name
    Write-Output "přeskočeno (chce správce): $($p.name)"
  } catch [System.UnauthorizedAccessException] {
    $needsAdmin += $p.name
    Write-Output "přeskočeno (chce správce): $($p.name)"
  }
}
foreach ($t in $tasks) {
  try {
    Disable-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -ErrorAction Stop | Out-Null
    Write-Output "vypnuta úloha: $($t.TaskPath)$($t.TaskName)"
  } catch {
    $needsAdmin += "$($t.TaskPath)$($t.TaskName)"
    Write-Output "přeskočeno (chce správce): $($t.TaskName)"
  }
}

if ($needsAdmin.Count) {
  Write-Output "`nZbývá ($($needsAdmin.Count)) — spusť PowerShell jako správce a skript znovu:"
  foreach ($n in $needsAdmin) { Write-Output "  $n" }
}
Write-Output "`nHotovo. Vrácení zpět: cleanup-startup.ps1 -Undo"
