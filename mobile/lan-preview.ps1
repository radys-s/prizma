<#
.SYNOPSIS
    Pustí Prizmu tak, aby na ni dosáhl telefon ve stejné Wi-Fi.

.DESCRIPTION
    Náhled, ne instalace. Uvidíš layout, písma, dotykové cíle a jak se chová
    klávesnice. Neuvidíš instalaci na plochu ani offline režim — obojí chce
    HTTPS, a `http://192.168.…` secure context není.

    Server běží v popředí a končí Ctrl+C. Schválně: appka je tímhle dostupná
    všemu, co je ve stejné síti, a chrání ji jen čtyřmístný kód. Na pozadí by
    se dala snadno zapomenout běžet.

    Naplánovaná úloha `Prizma AutoStart` se nemění a dál poslouchá jen na
    127.0.0.1. Pokud běží, drží port — skript to pozná a řekne, co s tím.

.PARAMETER Port
    Port serveru. Výchozí 8000, stejný jako úloha na pozadí.

.PARAMETER OpenFirewall
    Přidá příchozí pravidlo pro daný port. Jen privátní profil, jen TCP, jen
    ten port. Chce admin. Smaže se `-CloseFirewall`.

.PARAMETER CloseFirewall
    Smaže pravidlo, které vytvořil `-OpenFirewall`, a skončí. Chce admin.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\mobile\lan-preview.ps1
#>

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$OpenFirewall,
    [switch]$CloseFirewall
)

$ErrorActionPreference = 'Stop'

# Jméno je zároveň identifikátor: -CloseFirewall maže přesně tohle a nic jiného.
$RuleName = 'Prizma LAN preview'
$Repo = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Repo 'backend\.venv\Scripts\python.exe'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-LanAddress {
    # Adresa, kterou má počítač ve své síti. `PrefixOrigin -ne 'WellKnown'`
    # vyřadí loopback a APIPA; řadí se podle metriky rozhraní, takže když je
    # zapojený kabel i Wi-Fi, vyhraje to, kudy provoz opravdu teče.
    $candidates = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object {
            $_.PrefixOrigin -ne 'WellKnown' -and
            $_.IPAddress -notlike '127.*' -and
            $_.IPAddress -notlike '169.254.*'
        }
    if (-not $candidates) { return $null }

    $routes = Get-NetIPInterface -AddressFamily IPv4 -ErrorAction SilentlyContinue
    $best = $candidates | Sort-Object {
        $iface = $routes | Where-Object InterfaceIndex -eq $_.InterfaceIndex
        if ($iface) { $iface.InterfaceMetric } else { [int]::MaxValue }
    } | Select-Object -First 1
    return $best.IPAddress
}

# --- úklid firewallu a konec -------------------------------------------------

if ($CloseFirewall) {
    if (-not (Test-Admin)) {
        Write-Host 'Smazání pravidla chce admin PowerShell.' -ForegroundColor Yellow
        exit 1
    }
    $existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
    if ($existing) {
        Remove-NetFirewallRule -DisplayName $RuleName
        Write-Host "Pravidlo `"$RuleName`" smazáno." -ForegroundColor Green
    } else {
        Write-Host "Pravidlo `"$RuleName`" tu není, nic k mazání." -ForegroundColor Gray
    }
    exit 0
}

# --- kontroly prostředí ------------------------------------------------------

if (-not (Test-Path $Python)) {
    Write-Host "Chybí venv: $Python" -ForegroundColor Red
    Write-Host 'Vytvoř ho v backend/ a doinstaluj závislosti.' -ForegroundColor Gray
    exit 1
}
# Frontend se servíruje z disku (`spa.py` čte frontend/dist), takže bez buildu
# by telefon dostal starou verzi — nebo nic.
$Dist = Join-Path $Repo 'frontend\dist\index.html'
if (-not (Test-Path $Dist)) {
    Write-Host 'Chybí frontend/dist — spusť napřed `npm run build` ve frontend/.' -ForegroundColor Red
    exit 1
}

$ip = Get-LanAddress
if (-not $ip) {
    Write-Host 'Nenašel jsem adresu v síti. Jsi připojený k Wi-Fi?' -ForegroundColor Red
    exit 1
}

# Úloha na pozadí drží port. Sáhnout jí na to sám nechci — je to jediná věc,
# která appku spouští po přihlášení.
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "Port $Port už někdo poslouchá — nejspíš úloha `"Prizma AutoStart`"." -ForegroundColor Yellow
    Write-Host 'Zastav ji, nebo dej jiný port:' -ForegroundColor Gray
    Write-Host '    Stop-ScheduledTask -TaskName "Prizma AutoStart"' -ForegroundColor Gray
    Write-Host "    .\mobile\lan-preview.ps1 -Port 8001" -ForegroundColor Gray
    exit 1
}

# --- firewall ----------------------------------------------------------------

$rule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue

if ($OpenFirewall) {
    if (-not (Test-Admin)) {
        Write-Host 'Přidání pravidla chce admin PowerShell.' -ForegroundColor Yellow
        exit 1
    }
    if ($rule) {
        Write-Host "Pravidlo `"$RuleName`" už existuje." -ForegroundColor Gray
    } else {
        New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $Port -Profile Private | Out-Null
        Write-Host "Pravidlo `"$RuleName`" přidáno (TCP $Port, privátní profil)." -ForegroundColor Green
        Write-Host 'Po testu ukliď: -CloseFirewall' -ForegroundColor Gray
    }
} elseif (-not $rule) {
    Write-Host 'Firewall příchozí spojení nejspíš zahodí. Když se telefon nepřipojí:' -ForegroundColor Yellow
    Write-Host "    powershell -ExecutionPolicy Bypass -File .\mobile\lan-preview.ps1 -OpenFirewall" -ForegroundColor Gray
    Write-Host '(admin PowerShell)' -ForegroundColor Gray
    Write-Host ''
}

# --- spuštění ----------------------------------------------------------------

# `allowed_hostnames()` v hardening.py čte tohle za běhu. Bez toho by Host
# hlavička s IP neprošla a každý požadavek z telefonu by skončil na 421.
#
# JSON, ne holá adresa: pydantic-settings parsuje list-typed proměnné jako
# JSON, takže `ALLOWED_HOSTS=192.168.1.121` by shodilo start. Stejný důvod
# je popsaný u téhle proměnné v render.yaml.
$env:ALLOWED_HOSTS = "[`"$ip`"]"

# Bez tohohle by `enforce_origin` telefon odmítl 403 „Povoleno jen z tohoto
# počítače" dřív, než se stihne zeptat na kód — lokální režim pouští jen
# loopback. Výjimka bere jen privátní rozsahy a platí jen po dobu tohohle
# procesu; podrobně u `lan_preview` v backend/app/config.py.
$env:LAN_PREVIEW = 'true'

Write-Host ''
Write-Host '  Na telefonu otevři:' -ForegroundColor Cyan
Write-Host "    http://${ip}:$Port" -ForegroundColor White
Write-Host ''
Write-Host '  Přístupové okno je otevřené pro celou síť, chrání ho jen kód.' -ForegroundColor DarkYellow
Write-Host '  Instalace na plochu ani offline režim tady nepůjde — chce to HTTPS.' -ForegroundColor DarkGray
Write-Host '  Konec: Ctrl+C' -ForegroundColor DarkGray
Write-Host ''

# Uvicorn přímo, ne přes tools/startup/serve.py: ten si přesměrovává stdout
# i stderr do logu, protože ho spouští pythonw bez konzole. Tady je konzole
# to jediné, co chceme vidět — každý požadavek z telefonu se má objevit na
# obrazovce, a Ctrl+C má server zastavit, ne jen zavřít okno.
Push-Location (Join-Path $Repo 'backend')
try {
    & $Python -m uvicorn app.main:app --host 0.0.0.0 --port $Port
} finally {
    Pop-Location
}
